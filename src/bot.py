import asyncio
import io
import json
import os
import secrets
import zipfile

import aiosqlite
from PIL import Image
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, BufferedInputFile, InlineKeyboardMarkup, CallbackQuery, InputFile
from gspread import Cell
from gspread_asyncio import AsyncioGspreadClientManager
from qrcode import ERROR_CORRECT_L
from qrcode.main import QRCode
from google.oauth2.service_account import Credentials

from src.config import TELEGRAM_TOKEN, SQLITE_PATH, ROUNDS, SHEET_URL, ALL_POSITIONS, QUIZ_QUESTIONS, \
    QUIZ_BONUS_COEFFICIENTS
from src.filters import IsAdminFilter
from src.keyboards import create_round_keyboard
from src.models import Team, Game, RoundPosition
from src.states import UserState
from src.utils import is_float

bot = Bot(TELEGRAM_TOKEN)
dp = Dispatcher()

user_teams: dict[int, Team] = {}
game: Game = Game()
agcm: AsyncioGspreadClientManager

# Register

@dp.message(CommandStart(), F.text.split().len() == 2)
async def register_handler(message: Message, state: FSMContext):
    if message.from_user.id in user_teams:
        await message.answer("Вы уже зарегистрированы")
        return
    if game.round != 0 or game.started:
        await message.answer("Игра уже началсь")
        return
    team_id = message.text.split()[1]
    async with aiosqlite.connect(SQLITE_PATH) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM qrcodes WHERE id=?", (team_id,))
            row = await cur.fetchone()
        if not row:
            await message.answer("Неверный QR-код")
            return
        if row[1]:
            await message.answer("QR-код уже активирован")
            return
        await conn.execute("UPDATE qrcodes SET activated=1 WHERE id=?", (team_id,))
        await conn.commit()
    user_teams[message.from_user.id] = Team(team_id, f"Team #{team_id}", message.from_user.id)
    await save_team_to_db(user_teams[message.from_user.id])
    await message.answer("Назовите свою команду")
    await state.set_state(UserState.team_name)

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("Добро пожаловать, отсканируйте QR-код чтобы присоединиться к игре")

@dp.message(UserState.team_name)
async def set_name_handler(message: Message, state: FSMContext):
    if message.from_user.id not in user_teams:
        await state.clear()
        return
    team = user_teams[message.from_user.id]
    team.name = message.text
    await message.answer("Вы успешно зарегистрировались")
    await state.clear()
    await update_google_sheets(False)
    await update_team_in_db(team)

# Admin

@dp.message(Command("start_quiz"), IsAdminFilter())
async def start_quiz_handler(message: Message):
    if game.started:
        await message.answer("Текущий раунд ещё не завершён (/stop)")
        return
    if game.quiz_started:
        await message.answer("Квиз уже идёт (/end_quiz)")
        return
    game.quiz_started = True
    await message.answer("Квиз начат")
    await broadcast(f"Начинаем квиз, введите ответ на вопрос одним словом\n{QUIZ_QUESTIONS[0][0]}")

@dp.message(Command("end_quiz"), IsAdminFilter())
async def end_quiz_handler(message: Message):
    if not game.quiz_started:
        await message.answer("Квиз ещё не начался")
        return
    game.quiz_started = False
    await message.answer("Квиз окончен, используйте /quiz_results для подведения итогов")

@dp.message(Command("quiz_results"), IsAdminFilter())
async def quiz_results_handler(message: Message):
    for team in user_teams.values():
        if not team.quiz_answers:
            continue
        total_answers = 0
        for i, answer in enumerate(team.quiz_answers):
            if answer.lower() in list(map(str.lower, QUIZ_QUESTIONS[i][1])):
                total_answers += 1
        coefficient = QUIZ_BONUS_COEFFICIENTS[total_answers-1] if total_answers > 0 else 1
        old_asset_1, old_asset_2 = team.asset_1, team.asset_2
        team.asset_1 = round(team.asset_1 * coefficient, 2)
        team.asset_2 = round(team.asset_2 * coefficient, 2)
        await bot.send_message(
            team.owner_id,
           f"Вы ответили правильно на {total_answers} / {len(QUIZ_QUESTIONS)} вопросов"
            f"\nАктив I: {old_asset_1} * {coefficient} -> {team.asset_1}"
            f"\nАктив II: {old_asset_2} * {coefficient} -> {team.asset_2}"
        )
        team.quiz_answers.clear()
        if total_answers > 0:
            await update_team_in_db(team)
    await update_google_sheets(False)
    await message.answer("Результаты оглашены")

@dp.message(Command("next"), IsAdminFilter())
async def next_handler(message: Message):
    if game.started:
        await message.answer("Текущий раунд ещё не завершён (/stop)")
        return
    if game.quiz_started:
        await message.answer("Для начала завершите квиз (/end_quiz)")
        return
    if game.round == len(ROUNDS):
        await message.answer("Это был последний раунд")
        return

    game.round += 1
    game.started = True

    for i in 1,2: # asset 1 and 2
        await broadcast(
            f"Раунд {game.round}.\nВо что вложиться {'I' if i == 1 else 'II'} активом?",
            create_round_keyboard(game.round, i, None)
        )
    await update_game_in_db()
    await message.answer(f"Начинаем {game.round} раунд")

@dp.message(Command("stop"), IsAdminFilter())
async def stop_handler(message: Message):
    if not game.started and not game.wait_for_coefficient:
        await message.answer("Текущий раунд уже завершён")
        return
    game.started = False

    for position in ROUNDS[game.round-1]:
        if position.custom_coefficient is not None:
            args = message.text.split()
            if len(args) != 2 or not is_float(args[1]):
                game.wait_for_coefficient = True
                await message.answer("Для этого раунда необходим кастомный коэффициент, используйте: /stop [КОЭФФИЦИЕНТ]")
                return
            position.custom_coefficient_value = float(args[1])

    game.wait_for_coefficient = False
    for team in user_teams.values():
        old_asset_1, old_asset_2 = team.asset_1, team.asset_2
        coef_1, coef_2 = 1, 1
        if team.choice_1:
            coef_1 = round(get_pos_by_id(team.choice_1).get_coefficient(list(user_teams.values())), 2)
            team.asset_1 *= coef_1
        if team.choice_2:
            coef_2 = round(get_pos_by_id(team.choice_2).get_coefficient(list(user_teams.values())), 2)
            team.asset_2 *= coef_2
        team.asset_1, team.asset_2 = round(team.asset_1, 2), round(team.asset_2, 2)
        name_1 = get_pos_by_id(team.choice_1).name if team.choice_1 else "Не выбрано"
        name_2 = get_pos_by_id(team.choice_2).name if team.choice_2 else "Не выбрано"
        await bot.send_message(
            team.owner_id,
            f"Итоги торгов:"
            f"\nАктив I ({name_1}): {old_asset_1} * {coef_1} -> {team.asset_1}"
            f"\nАктив II ({name_2}): {old_asset_2} * {coef_2} -> {team.asset_2}"
        )
        await update_team_in_db(team)
    for pos in ROUNDS[game.round-1]:
        game.history.setdefault(pos.id, {})
        game.history[pos.id][str(game.round)] = (
            pos.get_invests_by_id(pos.id, user_teams.values()),
            pos.get_coefficient(user_teams.values()) or "-"
        )
    for team in user_teams.values():
        team.choice_1 = team.choice_2 = None
        await update_team_in_db(team)
    await update_google_sheets()
    await update_game_in_db()
    await message.answer("Раунд завершён")

@dp.message(Command("multiply"), IsAdminFilter())
async def multiply_handler(message: Message):
    args = message.text.split()
    if len(args) == 4:
        args[3] = args[3].replace(",", ".")
    if len(args) != 4 or args[2] not in ("1", "2") or not is_float(args[3]):
        await message.answer("Используйте: /multiply [ID КОМАНДЫ] [АКТИВ: 1-2] [МУЛЬТИПЛИКАТОР]")
        return
    team = get_team_by_id(args[1])
    if not team:
        await message.answer("Неверный ID команды")
        return
    if args[2] == "1":
        team.asset_1 *= float(args[3])
    else:
        team.asset_2 *= float(args[3])
    await message.answer(f"Новое значение актива: {team.asset_1 if args[2] == "1" else team.asset_2}")
    await update_google_sheets(False)
    await update_team_in_db(team)

@dp.message(Command("qrs"), IsAdminFilter())
async def qrs_handler(message: Message):
    args = message.text.split()[1:]
    if not args or not args[0].isdigit():
        await message.answer("Используйте: /qrs [QR_COUNT]")
        return
    qrs = [[secrets.token_hex(3)] for _ in range(int(args[0]))]
    async with aiosqlite.connect(SQLITE_PATH) as conn:
        await conn.executemany("INSERT INTO qrcodes (id) VALUES (?)", qrs)
        await conn.commit()
    file = io.BytesIO()
    me = await bot.get_me()
    with zipfile.ZipFile(file, "w") as zf:
        qr_template = Image.open("qr_template.png")
        for qr in qrs:
            qr_file = io.BytesIO()
            qr_data = QRCode(
                error_correction=ERROR_CORRECT_L,
                box_size=10,
                border=0,
            )
            qr_data.add_data(f"https://t.me/{me.username}?start={qr[0]}")
            qr_data.make()
            qr_code_img = qr_data.make_image(fill_color="white", back_color="#141414").resize((750, 750))
            img = qr_template.copy()
            img.paste(qr_code_img, (150, 650))
            img.save(qr_file, format="PNG")
            qr_file.seek(0)
            zf.writestr(f"{qr[0]}.jpeg", qr_file.read())
    file.seek(0)
    await message.answer_document(BufferedInputFile(file.read(), filename="qrs.zip"))

@dp.message(Command("send"), IsAdminFilter())
async def send_handler(message: Message):
    photo = message.photo
    args = (message.text or message.caption).split()[1:]
    if not args and not photo:
        await message.answer("Используйте: /send [TEXT] или прикрепите фотографию")
        return
    if photo:
        photo_file = io.BytesIO()
        await bot.download(photo[-1], photo_file)
        photo_file.seek(0)
    await broadcast(" ".join(args), photo=BufferedInputFile(photo_file.read(), filename="photo.png") if photo else None)

@dp.message(Command("stat"), IsAdminFilter())
async def stats_handler(message: Message):
    text = "Топ команд:\n\n"
    for i, team in enumerate(sorted(user_teams.values(), key=lambda x: x.total_score, reverse=True), 1):
        text += f"{i}. {team.name} ({team.total_score}) [{team.id}]\n"
    text += "\nКомпании:"
    for position in ALL_POSITIONS:
        position_history = game.history.get(position.id, None)
        if not position_history:
            continue
        text += f"\n\n{position.name}:\n" + "\n".join([f"{round}. {round_data[1]}x ({round_data[0]})" for round, round_data in position_history.items()])
    await message.answer(text)

@dp.message(Command("help"), IsAdminFilter())
async def help_handler(message: Message):
    await message.answer(
        "Список админ-команд:"
        "\n/next - Следующий раунд"
        "\n/stop - Завершить раунд"
        "\n/start_quiz - Начать квиз"
        "\n/end_quiz - Закончить квиз"
        "\n/quiz_results - Огласить результаты квиза"
        "\n/send [TEXT or PHOTO] - Отправка рассылки всем участникам"
        "\n/multiply [ID КОМАНДЫ] [АКТИВ: 1-2] [МУЛЬТИПЛИКАТОР] - Мультипликация актива команды"
        "\n/stat - Текстовое представление табличной статистики"
        "\n/qrs [QR_COUNT] - Генерация QR-кодов для регистрации"
    )

# Game

@dp.callback_query(F.data.startswith("invest"))
async def invest_handler(query: CallbackQuery):
    if query.from_user.id not in user_teams:
        return
    if not game.started:
        await query.answer("Торги уже закончились")
        return
    round_id, pos_id, asset = query.data.split(":")[1:]
    if int(round_id) != game.round:
        await query.answer("Торги уже закончились")
        return
    team = user_teams[query.from_user.id]
    if asset == "1":
        team.choice_1 = pos_id
        await query.message.edit_reply_markup(reply_markup=create_round_keyboard(game.round, 1, team.choice_1))
    else:
        team.choice_2 = pos_id
        await query.message.edit_reply_markup(reply_markup=create_round_keyboard(game.round, 2, team.choice_2))
    await query.answer()
    await update_team_in_db(team)

@dp.message()
async def quiz_handler(message: Message):
    if message.from_user.id not in user_teams:
        return
    if not game.quiz_started:
        return
    team = user_teams[message.from_user.id]
    if len(team.quiz_answers) == len(QUIZ_QUESTIONS):
        return
    team.quiz_answers.append(message.text)
    if len(team.quiz_answers) != len(QUIZ_QUESTIONS):
        await message.answer(QUIZ_QUESTIONS[len(team.quiz_answers)][0])
    else:
        await message.answer("Ответы приняты")

# Functions

def get_team_by_id(team_id: str) -> Team | None:
    return next(filter(lambda team: team.id == team_id.lower(), user_teams.values()), None)

def get_pos_by_id(pos_id: str) -> RoundPosition:
    return next(filter(lambda p: p.id == pos_id, ALL_POSITIONS))

async def update_google_sheets(update_positions: bool = True) -> None:
    agc = await agcm.authorize()
    spreadsheet = await agc.open_by_url(SHEET_URL)

    sheet = await spreadsheet.get_worksheet(0)
    rows = [["Место", "Имя", "Сумма", "ID"]]
    for i, team in enumerate(sorted(user_teams.values(), key=lambda x: x.total_score, reverse=True), 1):
        rows.append([i, team.name, team.total_score, team.id])
    await sheet.clear()
    await sheet.insert_rows(rows)

    if update_positions:
        sheet = await spreadsheet.get_worksheet(1)
        cells = []
        for i, position in enumerate(ALL_POSITIONS):
            position_history = game.history.get(position.id, None)
            if not position_history:
                continue
            for round, round_data in position_history.items():
                cells.append(Cell(4*i + 1 + 2, int(round) + 1, str(round_data[0])))
                cells.append(Cell(4*i + 1 + 3, int(round) + 1, str(round_data[1])))
        await sheet.update_cells(cells)


async def save_team_to_db(team: Team) -> None:
    async with aiosqlite.connect(SQLITE_PATH) as conn:
        await conn.execute("INSERT INTO teams VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (team.id, team.name, team.owner_id, team.asset_1, team.asset_2, team.choice_1, team.choice_2, json.dumps(team.quiz_answers)))
        await conn.commit()

async def update_team_in_db(team: Team) -> None:
    async with aiosqlite.connect(SQLITE_PATH) as conn:
        await conn.execute("UPDATE teams SET name=?, asset_1=?, asset_2=?, choice_1=?, choice_2=?, quiz_answers=? WHERE id=?", (team.name, team.asset_1, team.asset_2, team.choice_1, team.choice_2, json.dumps(team.quiz_answers), team.id))
        await conn.commit()

async def update_game_in_db() -> None:
    async with aiosqlite.connect(SQLITE_PATH) as conn:
        await conn.execute("UPDATE game SET round=?, started=?, history=?", (game.round, game.started, json.dumps(game.history)))
        await conn.commit()

async def load_game(conn: aiosqlite.Connection):
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM game")
        row = await cur.fetchone()
    if not row:
        await conn.execute("INSERT INTO game (round, started, history) VALUES (?, ?, ?)", (game.round, game.started, json.dumps(game.history)))
        await conn.commit()
    else:
        game.round, game.started, game.history = row
        game.history = json.loads(game.history)

async def load_teams(conn: aiosqlite.Connection):
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM teams")
        rows = await cur.fetchall()
        for row in rows:
            user_teams[row[2]] = Team(*row)
            user_teams[row[2]].quiz_answers = json.loads(row[7])

async def broadcast(
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        photo: InputFile | None = None
) -> None:
    tasks = [
        bot.send_message(team.owner_id, text, reply_markup=markup)
        if not photo else bot.send_photo(team.owner_id, photo, caption=text)
        for team in user_teams.values()
    ]
    await asyncio.gather(*tasks)

def get_creds():
    creds = Credentials.from_service_account_file("creds.json")
    scoped = creds.with_scopes([
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ])
    return scoped

async def main():
    global agcm
    if not os.path.exists(os.getcwd() + "/data"):
        os.mkdir("data")
    async with aiosqlite.connect(SQLITE_PATH) as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS qrcodes (id TEXT PRIMARY KEY, activated BOOL DEFAULT FALSE)")
        await conn.execute("CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id BIGINT NOT NULL, asset_1 FLOAT NOT NULL, asset_2 FLOAT NOT NULL, choice_1 TEXT, choice_2 TEXT, quiz_answers JSON NOT NULL)")
        await conn.execute("CREATE TABLE IF NOT EXISTS game (round INT NOT NULL, started BOOL NOT NULL, history JSON NOT NULL)")

        await load_game(conn)
        await load_teams(conn)
        await conn.commit()
    agcm = AsyncioGspreadClientManager(get_creds)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
