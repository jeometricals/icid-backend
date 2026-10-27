from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx

from scripts.backfill_tax_map_pdfs import main

# ---------------------------------------------------------------------------
# scripts/backfill_tax_map_pdfs.py (Slice Y1.5): the DOF tax-map PDFs of the sidewalks made before Y1.5. DOF, Storage
# and the statements are mocked.
# ---------------------------------------------------------------------------

PROPS = "api.queries.properties.run_query"
GET = "api.services.dof_tax_map.httpx.get"
STORE = "api.services.dof_tax_map"
MAP_648 = "30064820081204120000"


def site(n: int, block: str, lot: str, address: str) -> dict:
    """A sidewalk without a tax-map PDF, as the backfill's list returns it."""
    return {"id": UUID(f"e0000000-0000-4000-8000-00000000000{n}"), "project_id": "HWS0023", "address": address,
            "boro": "Brooklyn", "block": block, "lot": lot}


SITES = [site(1, "648", "26", "152 23 ST"), site(2, "648", "27", "154 23 ST"), site(3, "37502", "52", "350 Main St")]


def library(*maps: tuple[str, str]) -> MagicMock:
    """DOF's map library answering with the maps given."""
    response = MagicMock()
    response.json.return_value = {"features": [{"attributes": {"ID": i, "EFFECTIVE_DATE": d}} for i, d in maps]}
    return response


def portal() -> MagicMock:
    """DOF's portal answering with a PDF."""
    response = MagicMock()
    response.content = b"%PDF-1.7 block 648"
    return response


def test_a_dry_run_looks_each_block_up_once_and_writes_nothing(capsys):
    with patch(PROPS, return_value=SITES) as run, patch(GET, side_effect=[library((MAP_648, "2008/12/04")), library()]) as get, \
            patch(f"{STORE}.upload_file") as upload:
        assert main(commit=False, pause=0) == 0
    assert get.call_count == 2 and run.call_count == 1  # one lookup per block, no download, no write
    upload.assert_not_called()
    out = capsys.readouterr().out
    assert f"Brooklyn block 648: map {MAP_648}, effective 2008-12-04" in out
    assert "HWS0023 152 23 ST (BBL 3006480026): would get it" in out and "154 23 ST (BBL 3006480027): would get it" in out
    assert "Brooklyn block 37502: no current map in DOF's library" in out
    assert "dry run complete: 2 block(s) looked up, 1 site(s) on a block DOF has no map for" in out


def test_commit_downloads_a_blocks_map_once_and_stores_it_for_each_site(capsys):
    recorded = {"old_tax_map_pdf_path": None}
    with patch(PROPS, side_effect=[SITES[:2], [recorded], [recorded]]) as run, \
            patch(GET, side_effect=[library((MAP_648, "2008/12/04")), portal()]) as get, \
            patch(f"{STORE}.upload_file") as upload:
        assert main(commit=True, pause=0) == 0
    assert get.call_count == 2  # one lookup, one download, for two sites
    assert [call.args[1] for call in upload.call_args_list] == [f"{SITES[0]['id']}/{MAP_648}.pdf",
                                                                f"{SITES[1]['id']}/{MAP_648}.pdf"]
    assert run.call_args.args[1][:3] == (f"{SITES[1]['id']}/{MAP_648}.pdf", "dof_map_library", MAP_648)
    assert "backfill complete: 1 block(s) looked up, 2 site(s) stored" in capsys.readouterr().out


def test_a_skipped_bbl_is_left_out_and_a_block_of_skipped_sites_isnt_asked_about(capsys):
    with patch(PROPS, return_value=SITES), patch(GET, side_effect=[library((MAP_648, "2008/12/04"))]) as get:
        assert main(commit=False, skip_bbls=frozenset({"3375020052"}), pause=0) == 0
    assert get.call_count == 1
    out = capsys.readouterr().out
    assert "350 Main St (BBL 3375020052): skipped (--skip-bbl); nothing written" in out
    assert "1 skipped" in out


def test_dof_down_fails_that_block_and_carries_on(capsys):
    with patch(PROPS, return_value=SITES), patch(GET, side_effect=[httpx.ConnectError("down"), library()]):
        assert main(commit=False, pause=0) == 1
    assert "2 site(s) failed and were left as they were" in capsys.readouterr().out


def test_a_list_that_cant_be_read_is_1():
    with patch(PROPS, return_value=None):
        assert main(commit=True, pause=0) == 1
