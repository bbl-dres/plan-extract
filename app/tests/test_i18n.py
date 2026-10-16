"""The interface texts (app/i18n.json): every key the page and its modules use exists in German, French, Italian and
English, with the same {placeholders} in every language; no key is left unused; the server serves the file.

    python -m pytest app/tests -q      (from the repository root)
"""
import json
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TEXTS = json.loads((APP / "i18n.json").read_text(encoding="utf-8"))
LANGS = {"de", "fr", "it", "en"}
VALUE_GROUPS = ("conf.", "construction.", "kind.", "region.", "severity.", "wallKind.")   # tv(): values from the pipeline, looked up by group


def used_keys():
    """The keys of t() and tn() calls, of data-i18n attributes and of the key lists (stages, dpi sources, layers,
    scale cases), plus the dynamic families: the job states and the panel edge labels."""
    keys = set()
    for f in [*APP.glob("js/*.js"), *APP.glob("js/screens/*.js"), APP / "index.html", APP / "api-docs.html"]:
        s = f.read_text(encoding="utf-8")
        keys |= {k for k in re.findall(r"\bt\('([\w.]+)'", s) if not k.endswith(".")}   # t('status.' + st): a family, below
        keys |= {f"{k}.{n}" for k in re.findall(r"\btn\('([\w.]+)'", s) for n in ("one", "other")}
        keys |= set(re.findall(r'data-i18n(?:-html|-title|-aria-label)?="([\w.]+)"', s))
        keys |= set(re.findall(r"'((?:stage|dpi|layers|scale\.case|error)\.[\w.]+)'", s))
        keys |= set(re.findall(r"\? '(upload\.\w+)' : '(?:upload\.\w+)'", s)) | set(re.findall(r"\? '(?:upload\.\w+)' : '(upload\.\w+)'", s))
    keys |= {f"status.{s}" for s in ("analysing", "scaling", "queued", "running", "ready", "done", "error")}
    keys |= {f"results.{a}{b}" for a in ("hide", "show") for b in ("Layers", "Results")}
    return {k for k in keys if f"{k}.one" not in TEXTS}       # a plural family is used through its .one and .other


def test_every_key_used_exists_and_none_is_unused():
    used = used_keys()
    assert sorted(used - set(TEXTS)) == []
    assert sorted(k for k in set(TEXTS) - used if not k.startswith(VALUE_GROUPS)) == []


def test_every_text_in_four_languages_with_the_same_placeholders():
    placeholders = lambda s: sorted(set(re.findall(r"\{(\w+)\}", s)))
    for key, texts in TEXTS.items():
        assert set(texts) == LANGS, key
        assert all(str(v).strip() for v in texts.values()), key
        for lang in LANGS - {"en"}:
            assert placeholders(texts[lang]) == placeholders(texts["en"]), (key, lang)


def test_markup_only_in_html_texts():
    """Texts go into the page as plain text, except the data-i18n-html ones: only those may carry markup."""
    html = set(re.findall(r'data-i18n-html="([\w.]+)"', (APP / "index.html").read_text(encoding="utf-8") + (APP / "api-docs.html").read_text(encoding="utf-8")))
    for key, texts in TEXTS.items():
        if key not in html:
            assert not any(re.search(r"<\w", v) for v in texts.values()), key


def test_no_local_name_hides_the_text_functions():
    """A module that imports t, tn or tv must not declare a local of the same name: `const t = ...` once broke every
    notice and the results screen."""
    for f in [*APP.glob("js/*.js"), *APP.glob("js/screens/*.js")]:
        if f.name == "i18n.js":
            continue
        s = f.read_text(encoding="utf-8")
        names = set()
        for m in re.finditer(r"import \{([^}]*)\} from '\.\.?/i18n\.js'", s):
            names |= {n.strip() for n in m.group(1).split(",")}
        if not names:
            continue
        alt = "|".join(sorted(names))
        decls = re.findall(rf"\b(?:const|let|var|function)\s+({alt})\b", s)
        params = [p for group in re.findall(r"\(([^()]*)\)\s*=>", s) for p in re.split(r"[,\s=]+", group) if p in names]
        params += re.findall(rf"(?<![\w.])({alt})\s*=>", s)
        assert not decls and not params, (f.name, decls, params)


def test_served(served):
    from test_server import call
    base, _, _ = served
    status, body = call(base, "/i18n.json")
    assert status == 200 and body["app.title"]["de"] == TEXTS["app.title"]["de"]
