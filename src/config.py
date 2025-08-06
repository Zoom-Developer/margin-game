import os

import dotenv

from src.models import RoundPosition

dotenv.load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(";")))
SHEET_URL = os.getenv("SHEET_URL")

SQLITE_PATH = "data/database.db"

tbank = RoundPosition(id="tbank", name="Т-Банк", linear_coefficient=lambda _: 1.1)
sibur = RoundPosition(id="sibur", name='Сибур', linear_coefficient=lambda n: 25 / (n or 1))
vk = RoundPosition(id="vk", name='VK', linear_coefficient=lambda n: 15 / (n or 1))
crypto = RoundPosition(
    id="crypto",
    name="Крипта",
    nonlinear_coefficients={
        (1, 2): 1.5,
        (3, 4): 2,
        (5, 6): 3,
        (7, 8): 6,
        (9, None): 0.3
    }
)
kinopoisk = RoundPosition(
    id="kinopoisk",
    name='Кинопоиск',
    nonlinear_coefficients={
        (1, 2): 4,
        (3, 4): 3,
        (5, 6): 2,
        (7, 8): 1.5,
        (9, None): 0.8
    }
)
djara = RoundPosition(
    id="djara",
    name="Djara",
    nonlinear_coefficients={
        (4, 7): 10,
        (1, 3): 0.8,
        (8, None): 0.8
    }
)
vkplay = RoundPosition(
    id="vkplay",
    name='VK.Play',
    coefficient_from_mother="vk"
)
nft = RoundPosition(
    id="nft",
    name="NFT",
    custom_coefficient=True
)

ALL_POSITIONS = [tbank, sibur, vk, crypto, kinopoisk, djara, vkplay, nft]

ROUNDS = [
    [
        tbank,
        sibur,
        vk,
        crypto
    ],
    [
        tbank,
        sibur,
        vk,
        kinopoisk,
        crypto
    ],
    [
        tbank,
        sibur,
        vk,
        kinopoisk,
        crypto,
        djara
    ],
    [
        tbank,
        sibur,
        vk,
        crypto,
        djara,
        vkplay
    ],
    [
        tbank,
        sibur,
        vk,
        nft,
        djara,
        vkplay
    ],
    [
        tbank,
        sibur,
        vk,
        nft,
        djara,
        vkplay
    ]
]

QUIZ_QUESTIONS = [
    ("В переводе с одного из языков название этой компании означает чувство зависти. И действительно, продукты компании пережили такой резкий скачок цен в 2021 году, что некоторые эксперты окрестили этот период зеленой лихорадкой. Впрочем, сложно сказать, что лихорадка закончилась и сейчас - капитализация компании бьет новые рекорды.\n\nНазовите компанию", ("nvidia", "нвидиа", "нвидия")),
    ("В японских садах принято любоваться сакурой, не срывая цветы. Так и некоторые инвесторы предпочитают лишь наблюдать, как на их счёт регулярно «падают лепестки», ведь иногда цветение может происходить до 4 раз в год. Как одним словом они называют эти «лепестки»?", ("дивиденды", ))
]
QUIZ_BONUS_COEFFICIENTS = (1.1, 1.21) # count of correct answers