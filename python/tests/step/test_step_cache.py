"""The text route, the model cache, the worker pool and hooks give the same results as reading the file whole."""

import json
import multiprocessing

import pytest
from stepkit import TINY, TINY_POS, require_occ

require_occ()

from boarddd.step import hlr, work  # noqa: E402
from boarddd.step import index as stepindex  # noqa: E402
from boarddd.step.cache import Store  # noqa: E402
from boarddd.step.split import StepTooLarge, split  # noqa: E402


def plain(assembly) -> dict:
    data = assembly.to_dict()
    data.pop("timings")
    return json.loads(json.dumps(data))


def faces(model) -> dict:
    """A stand-in for magpie's footprint-check contacts: anything worked out from the loaded solid."""
    from boarddd.step.measure import face_type_counts

    total, kinds = face_type_counts(model.shape)
    return {"faces": total, "kinds": kinds}


@pytest.fixture
def hook():
    work.register_hook("faces", faces)
    yield "faces"
    work.HOOKS.pop("faces", None)


def test_text_route_matches_reading_the_file_whole():
    whole = split(TINY, pos=TINY_POS, text=False, cache=False)
    textual = split(TINY, pos=TINY_POS, cache=False, workers=1)
    assert whole.read_from == "occt" and textual.read_from == "text"
    a, b = plain(whole), plain(textual)
    assert a["board"] == b["board"] and a["seat_z"] == b["seat_z"]
    for ref, component in a["components"].items():
        other = b["components"][ref]
        assert {k: v for k, v in component.items() if k != "model"} == {
            k: v for k, v in other.items() if k != "model"
        }, ref
        first, second = a["models"][component["model"]], b["models"][other["model"]]
        assert first["fingerprint"] == second["fingerprint"], ref
        assert first["measurements"] == second["measurements"], ref


def test_cached_is_identical_to_uncached(tmp_path, hook):
    uncached = split(TINY, pos=TINY_POS, cache=False, workers=1)
    cold = split(TINY, pos=TINY_POS, cache=tmp_path, workers=1)
    warm = split(TINY, pos=TINY_POS, cache=tmp_path, workers=1)
    assert cold.stats["worked"] == len(cold.models) and warm.stats["worked"] == 0
    assert plain(uncached) == plain(cold) == plain(warm)
    for ref in uncached.components:
        a, b, c = uncached.components[ref], cold.components[ref], warm.components[ref]
        assert a.step_bytes() == b.step_bytes() == c.step_bytes(), ref
        assert a.glb_bytes() == b.glb_bytes() == c.glb_bytes(), ref
        # the hook ran with the model, and the warm board read its result from the cache
        assert a.model.extras[hook] == c.model.extras[hook] == faces(a.model), ref
    assert list(tmp_path.rglob("faces.json"))
    assert hlr.drawing(uncached.components["U1"]) == hlr.drawing(warm.components["U1"])
    # The warm board read nothing but its text: no model needed OpenCascade's reader.
    assert all(model._label is None for model in warm.models.values())


def test_hook_names_are_checked():
    with pytest.raises(ValueError):
        work.register_hook("glb", faces)


def test_the_pool_gives_what_one_process_gives(hook):
    one = split(TINY, pos=TINY_POS, cache=False, workers=1)
    pool = split(TINY, pos=TINY_POS, cache=False, workers=3)
    assert plain(one) == plain(pool)
    assert {k: m.extras for k, m in one.models.items()} == {k: m.extras for k, m in pool.models.items()}


def test_a_model_is_the_same_on_another_board(tmp_path):
    """The same stock model on two boards has one identity: here the board and its own export of R1."""
    found = {}
    r1 = split(TINY, pos=TINY_POS, cache=False).components["R1"]
    for data in (TINY.read_bytes(), r1.step_bytes()):
        structure = stepindex.structure(stepindex.Index(data))
        for occurrence in structure.children.get(structure.root, []):
            found.setdefault(structure.names[occurrence.child], set()).add(
                stepindex.identity(structure, occurrence.child)
            )
    assert found["R_0402_1005Metric"] and found["R_0402_1005Metric"] != found["R_0603_1608Metric"]


def test_a_carved_product_stands_alone():
    index = stepindex.Index(TINY.read_bytes())
    structure = stepindex.structure(index)
    whole = split(TINY, pos=TINY_POS, text=False, cache=False)
    occurrence = next(o for o in structure.children[structure.root] if o.name == "U2")
    carved = stepindex.carve(structure, occurrence.child)
    assert len(carved) < len(TINY.read_bytes()) / 3
    (alone,) = split(carved, cache=False).components.values()
    assert alone.fingerprint.shape_key == whole.components["U2"].fingerprint.shape_key
    assert alone.fingerprint.color_key == whole.components["U2"].fingerprint.color_key


def test_size_guards(monkeypatch):
    monkeypatch.setenv("BOARDDD_STEP_MAX_MB", "0.1")
    with pytest.raises(StepTooLarge, match="BOARDDD_STEP_MAX_MB"):
        split(TINY, cache=False)
    monkeypatch.delenv("BOARDDD_STEP_MAX_MB")
    monkeypatch.setenv("MAGPIE_STEP_MAX_ENTITIES", "100")  # magpie's name still works
    with pytest.raises(StepTooLarge, match="entities"):
        split(TINY, cache=False)


def _split_into(cache: str) -> str:
    return json.dumps(plain(split(str(TINY), pos=str(TINY_POS), cache=cache, workers=1)), sort_keys=True)


def test_two_processes_can_fill_one_cache(tmp_path):
    with multiprocessing.get_context("fork").Pool(2) as pool:
        first, second = pool.map(_split_into, [str(tmp_path)] * 2)
    assert first == second
    assert not list(tmp_path.rglob(".*.*"))  # no temporary files left behind
    warm = split(TINY, pos=TINY_POS, cache=Store(tmp_path), workers=1)
    assert warm.stats["worked"] == 0
