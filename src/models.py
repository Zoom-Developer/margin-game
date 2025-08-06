from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Team:
    id: str
    name: str
    owner_id: int
    asset_1: float = 10
    asset_2: float = 10
    choice_1: str | None = None
    choice_2: str | None = None

    quiz_answers: list[str] = field(default_factory=list)

    @property
    def total_score(self):
        return self.asset_1 + self.asset_2

@dataclass
class Game:
    round: int = 0
    started: bool = False
    wait_for_coefficient: bool = False
    quiz_started: bool = False
    history: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict) # position: round: (N, earn)

@dataclass
class RoundPosition:
    name: str
    id: str
    linear_coefficient: Callable[[int], float] | None = None # N => coefficient
    nonlinear_coefficients: dict[tuple[int, int | None], float] | None = None # (from_N, to_N): coefficient
    coefficient_from_mother: str | None = None # mother id
    custom_coefficient: bool | None = None

    custom_coefficient_value: float | None = None

    def get_coefficient(self, teams: list[Team]) -> float | None:
        if self.linear_coefficient is not None:
            return round(self.linear_coefficient(self.get_invests_by_id(self.id, teams)), 2)
        if self.nonlinear_coefficients is not None:
            invests = self.get_invests_by_id(self.id, teams)
            for period, coefficient in self.nonlinear_coefficients.items():
                if (invests >= period[0] and period[1] is None) or period[0] <= invests <= period[1]:
                    return coefficient
        if self.coefficient_from_mother is not None:
            invest_count = self.get_invests_by_id(self.id, teams)
            if not invest_count:
                return 1
            return round((self.get_invests_by_id(self.coefficient_from_mother, teams)
                    / invest_count) or 1, 2)
        if self.custom_coefficient is not None:
            return self.custom_coefficient_value
        return None

    @staticmethod
    def get_invests_by_id(pos_id: str, teams: list[Team]) -> int:
        total = 0
        for team in teams:
            if team.choice_1 == pos_id:
                total += 1
            if team.choice_2 == pos_id:
                total += 1
        return total