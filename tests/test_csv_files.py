from datetime import datetime, timezone

from api.services.csv_files import csv_bytes, file_token, today_in_new_york


class TestCsvFiles:
    def test_a_csv_is_utf8_with_a_bom_crlf_and_quotes_what_needs_them(self):
        content = csv_bytes(["Scope", "Description"], [["138-22 227 Street", 'Sidewalk 4", reset'], ["A", ""]])
        assert content.startswith(b"\xef\xbb\xbf")
        assert content.decode("utf-8-sig") == ('Scope,Description\r\n138-22 227 Street,"Sidewalk 4"", reset"\r\n'
                                               'A,\r\n')

    def test_a_file_token_keeps_letters_digits_hyphens_and_underscores(self):
        assert file_token("TO-002") == "TO-002"
        assert file_token("TO 2 / Phase #1") == "TO_2_Phase_1"
        assert file_token("///") == "_"

    def test_today_is_new_yorks(self):
        # 03:00 UTC on 29 October is still the 28th in New York
        assert today_in_new_york(datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)) == "2026-10-28"
