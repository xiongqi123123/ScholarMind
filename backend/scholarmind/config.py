"""Settings: connections and secrets from the environment / .env, the rest from config.yaml."""

import logging
import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
OUT_OF_SCOPE = "out-of-scope"  # the domain of papers outside every other domain
log = logging.getLogger(__name__)


def topic_key(name: str) -> str:
    """What two topic names must share to be the same topic: case, spacing, hyphens and other
    punctuation and a plural "s" are ignored ("3D Gaussian-Splats" == "3d gaussian splat").
    """
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", name).casefold())
    return "".join(
        w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str
    redis_url: str
    secret_key: str
    cookie_secure: bool = False  # set true when served over HTTPS
    config_file: Path = ROOT / "config.yaml"
    llm_api_key: str = ""  # passed to pi only
    pi_coding_agent_dir: Path = ROOT / ".cache" / "pi-agent"  # pi's config, models.json generated
    prompts_dir: Path = ROOT / "prompts"
    frontend_dir: Path = ROOT / "frontend" / "dist"  # built web app; not served if missing

    @property
    def sqlalchemy_url(self) -> str:
        scheme, sep, rest = self.database_url.partition("://")
        if scheme in ("postgres", "postgresql"):
            scheme = "postgresql+psycopg"
        return scheme + sep + rest


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt key is an error, not ignored


class ArxivConfig(Section):
    categories: list[str]


class SyncConfig(Section):
    cron: str = "30 12 * * *"  # local time of the cron process (TZ)
    venues_cron: str = "0 4 * * 1"  # weekly check for new conference editions, same time zone


class TopicConfig(Section):
    name: str = Field(min_length=1)  # canonical, English
    zh: str = ""
    aliases: list[str] = []
    definition: str = ""  # one sentence; zh serves when it is empty


class DomainConfig(Section):
    zh: str
    topics: list[TopicConfig] = []


def _builtin_vocabulary() -> dict:
    return yaml.safe_load((Path(__file__).parent / "vocabulary.yaml").read_text(encoding="utf-8"))


class ReportAutoConfig(Section):
    """Labels that queue a full report once a paper's brief has them (any one matches)."""

    domains: list[str] = []
    topics: list[str] = []  # names or aliases, compared like topic names (topic_key)
    keywords: list[str] = []  # compared case-insensitively


class AnalysisConfig(Section):
    base_url: str  # OpenAI-compatible endpoint
    brief_model: str
    brief_batch_size: PositiveInt = 20
    report_model: str | None = None  # defaults to brief_model
    report_auto: ReportAutoConfig = ReportAutoConfig()
    report_daily_limit: NonNegativeInt = 10  # automatic reports per local day; manual not counted
    language: str = "Simplified Chinese"  # of brief summaries; reports follow their prompt
    topic_review_cron: str = "0 13 * * 1"  # weekly review of created topics, local time (TZ)

    @model_validator(mode="after")
    def _report_model_defaults_to_brief_model(self):
        self.report_model = self.report_model or self.brief_model
        return self


class VenueConfig(Section):
    """One conference: where its proceedings are (see docs/design/conference-sources.md) and
    what to expect. In url, path, title and doi, {year} stands for the edition's year.
    """

    adapter: Literal["openalex", "cvf", "proceedings", "pmlr"]
    every: PositiveInt = 1  # years between editions
    # Fewer main-conference papers than this in a full listing fails the import: the site has
    # probably changed. About half of the smallest edition since 2022.
    min_papers: PositiveInt
    url: str = ""  # cvf, proceedings: the listing page
    path: str = ""  # cvf: links to papers of this edition contain it
    source: str = ""  # openalex: source ID
    doi: str = ""  # openalex: regex the DOI must match; a group named track names the track
    issue_archive: str = ""  # openalex: OJS archive; only "Technical Track" issues are main
    min_pages: NonNegativeInt = 0  # openalex: shorter papers are demos, symposia and the like
    title: str = ""  # pmlr: the volume's title on the PMLR index
    exclude_title: str = ""  # regex: papers with matching titles are not main-conference
    # papers an edition has, about, for the estimate before an import (venues.estimate)
    papers_per_edition: PositiveInt | None = None  # None: twice min_papers
    held_in: PositiveInt | None = None  # every > 1: a year it was held, to know the others

    @model_validator(mode="after")
    def _adapter_parameters(self):
        needed = {"openalex": "source", "cvf": "url", "proceedings": "url", "pmlr": "title"}
        if not getattr(self, needed[self.adapter]):
            raise ValueError(f"the {self.adapter} adapter needs {needed[self.adapter]}")
        self.papers_per_edition = self.papers_per_edition or 2 * self.min_papers
        return self

    def held(self, year: int) -> bool:
        """Whether there is an edition this year; without held_in nothing says, so yes."""
        return self.held_in is None or (year - self.held_in) % self.every == 0


# Built-in conferences; config.yaml can override fields, add venues or drop them (Name: null).
# ponytail: papers_per_edition is the latest edition's count (main conference, from its listing
# or DBLP, 2026-11; RSS as imported), for cost estimates only: high for earlier editions, which
# were smaller. Better too high than too low for a cost warning.
_CVF = "https://openaccess.thecvf.com/"
VENUES = {
    "NeurIPS": dict(
        adapter="proceedings",
        url="https://papers.nips.cc/paper_files/paper/{year}",
        min_papers=1200,
        papers_per_edition=5824,
    ),
    "ICLR": dict(
        adapter="proceedings",
        url="https://proceedings.iclr.cc/paper_files/paper/{year}",
        min_papers=1000,
        papers_per_edition=5351,
    ),
    "ICML": dict(
        adapter="pmlr",
        title="Proceedings of ICML {year}",
        exclude_title="^Position:",  # the position paper track, in the same PMLR volume
        min_papers=600,
        papers_per_edition=6552,
    ),
    "CVPR": dict(
        adapter="cvf",
        url=_CVF + "CVPR{year}?day=all",
        path="/content/CVPR{year}/",
        min_papers=1000,
        papers_per_edition=4042,
    ),
    "ICCV": dict(
        adapter="cvf",
        url=_CVF + "ICCV{year}?day=all",
        path="/content/ICCV{year}/",
        every=2,
        held_in=2025,
        min_papers=1000,
        papers_per_edition=2701,
    ),
    "ECCV": dict(
        adapter="cvf",
        url="https://www.ecva.net/papers.php",
        path="/eccv_{year}/",
        every=2,
        held_in=2024,
        min_papers=800,
        papers_per_edition=2834,
    ),
    "ICRA": dict(adapter="openalex", source="S4210217939", min_papers=450, papers_per_edition=1874),
    "IROS": dict(adapter="openalex", source="S4393918188", min_papers=600, papers_per_edition=1986),
    "RSS": dict(adapter="openalex", source="S4306420803", min_papers=35, papers_per_edition=208),
    "ACL": dict(
        adapter="openalex",
        source="S4393915740",
        doi=r"^10\.18653/(v1/)?{year}\.acl-(?P<track>long|short)\.",
        min_papers=350,
        papers_per_edition=2296,
    ),
    "EMNLP": dict(
        adapter="openalex",
        source="S4363608991",
        doi=r"^10\.18653/(v1/)?{year}\.emnlp-main\.",
        min_papers=400,
        papers_per_edition=2704,
    ),
    "AAAI": dict(
        adapter="openalex",
        source="S4210191458",
        issue_archive="https://ojs.aaai.org/index.php/AAAI/issue/archive",
        min_papers=650,
        papers_per_edition=4040,
    ),
    "ACM MM": dict(
        adapter="openalex",
        source="S4363608757",
        min_pages=7,
        min_papers=350,
        papers_per_edition=1901,
    ),
}


class Config(Section):
    arxiv: ArxivConfig
    sync: SyncConfig = SyncConfig()
    # Domains and their starting topics; the built-in scholarmind/vocabulary.yaml if left out.
    vocabulary: dict[str, DomainConfig] = Field(
        default_factory=_builtin_vocabulary, validate_default=True
    )
    analysis: AnalysisConfig
    venues: dict[str, VenueConfig] = Field(default_factory=dict, validate_default=True)

    @field_validator("venues", mode="before")
    @classmethod
    def _merge_default_venues(cls, value):
        """Entries override the built-in VENUES field by field; `Name: null` drops a venue."""
        merged = dict(VENUES)
        for name, override in (value or {}).items():
            if override is None:
                merged.pop(name, None)
            else:
                merged[name] = {**VENUES.get(name, {}), **override}
        return merged

    @model_validator(mode="before")
    @classmethod
    def _ignore_the_old_tag_scheme(cls, data):
        """A config.yaml from before topics (vocabulary.domains/tasks lists, report_auto.tasks)
        still starts: those keys are ignored, with a warning.
        """
        if not isinstance(data, dict):
            return data
        vocabulary = data.get("vocabulary")
        if isinstance(vocabulary, dict) and isinstance(vocabulary.get("tasks"), list):
            log.warning("config: ignoring the old vocabulary (domains/tasks lists)")
            data = {key: value for key, value in data.items() if key != "vocabulary"}
        auto = (data.get("analysis") or {}).get("report_auto")
        if isinstance(auto, dict) and "tasks" in auto:
            log.warning("config: ignoring analysis.report_auto.tasks (tasks are now topics)")
            del auto["tasks"]
        return data

    @model_validator(mode="after")
    def _check_the_vocabulary(self):
        if OUT_OF_SCOPE not in self.vocabulary or self.vocabulary[OUT_OF_SCOPE].topics:
            raise ValueError(f"vocabulary needs the domain {OUT_OF_SCOPE}, without topics")
        if len(self.vocabulary) < 2:
            raise ValueError("vocabulary needs a domain besides " + OUT_OF_SCOPE)
        owner = {}
        for domain in self.vocabulary.values():
            for topic in domain.topics:
                for name in (topic.name, *topic.aliases):
                    other = owner.setdefault(topic_key(name), topic.name)
                    if other != topic.name:
                        raise ValueError(f"vocabulary: {name!r} of {topic.name!r} is {other!r}")
        if unknown := set(self.analysis.report_auto.domains) - set(self.vocabulary):
            raise ValueError(f"analysis.report_auto.domains not in the vocabulary: {unknown}")
        return self

    @property
    def domains(self) -> list[str]:
        """The domains a topic can belong to: all but out-of-scope."""
        return [domain for domain in self.vocabulary if domain != OUT_OF_SCOPE]


@cache
def settings() -> Settings:
    return Settings()


@cache
def config() -> Config:
    """config.yaml, validated. Every server process calls this at startup, so a missing
    section or a misspelt key stops it from starting instead of failing a later request.
    """
    return Config.model_validate(yaml.safe_load(settings().config_file.read_text()))
