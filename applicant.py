"""Applicant profile for "Apply for me": load applicant.local.yaml, validate it."""
import pathlib

import yaml

HERE = pathlib.Path(__file__).parent
STR_KEYS = ("name", "email", "phone", "city", "work_authorization",
            "notice_period", "salary_expectation", "heard_about_us", "cv_path")


class ProfileError(Exception):
    pass


def load(path):
    path = pathlib.Path(path)
    if not path.is_file():
        raise ProfileError(f"Applicant profile not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"Applicant profile is not valid YAML: {e}")
    if not isinstance(raw, dict):
        raise ProfileError("Applicant profile must be a YAML mapping")
    p = dict(raw)
    for k in STR_KEYS:
        v = p.get(k)
        p[k] = "" if v is None else str(v).strip()
    for k in ("links", "demographics"):
        v = p.get(k)
        p[k] = dict(v) if isinstance(v, dict) else {}
    if not p["name"]:
        raise ProfileError("Applicant profile: 'name' is empty")
    if not p["email"]:
        raise ProfileError("Applicant profile: 'email' is empty")
    cv = pathlib.Path(p["cv_path"]).expanduser() if p["cv_path"] else None
    if cv is not None and not cv.is_absolute():
        cv = HERE / cv
    if cv is None or cv.suffix.lower() != ".pdf" or not cv.is_file():
        raise ProfileError("Applicant profile: 'cv_path' must point to an existing .pdf file")
    p["cv_path"] = str(cv)
    return p


def facts_text(profile):
    lines = []
    for k, v in profile.items():
        if k == "cv_path":
            continue
        if isinstance(v, dict):
            for sk, sv in v.items():
                if sv not in ("", None):
                    lines.append(f"{k}.{sk}: {sv}")
        elif v not in ("", None):
            lines.append(f"{k}: {v}")
    return "\n".join(lines)
