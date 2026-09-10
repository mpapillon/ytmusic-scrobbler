"""Unit tests for HistorySong.from_api_item(): normalization of raw ytmusicapi
get_history() entries, and the drop rules (missing title/artist, ' - Topic' channels)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrobble_utils import HistorySong


def api_item(**overrides):
    item = {
        'title': 'Song',
        'artists': [{'name': 'Artist', 'id': 'UCxxx'}],
        'album': {'name': 'Album', 'id': 'MPREb_xxx'},
        'played': 'Today at 14:32',
    }
    item.update(overrides)
    return item


class TestHistorySongFromApiItem(unittest.TestCase):
    def test_full_item(self):
        song = HistorySong.from_api_item(api_item())
        self.assertEqual(
            song,
            HistorySong('Song', 'Artist', 'Album', played='Today at 14:32'),
        )

    def test_multi_artists_uses_first(self):
        item = api_item(artists=[{'name': 'First'}, {'name': 'Second'}])
        song = HistorySong.from_api_item(item)
        self.assertEqual(song.artist, 'First')

    def test_missing_album_falls_back_to_title(self):
        song = HistorySong.from_api_item(api_item(album=None))
        self.assertEqual(song.album, 'Song')

    def test_absent_album_falls_back_to_title(self):
        item = api_item()
        del item['album']
        song = HistorySong.from_api_item(item)
        self.assertEqual(song.album, 'Song')

    def test_album_without_name_falls_back_to_title(self):
        song = HistorySong.from_api_item(api_item(album={'id': 'MPREb_xxx'}))
        self.assertEqual(song.album, 'Song')

    def test_missing_played_becomes_empty_string(self):
        item = api_item()
        del item['played']
        song = HistorySong.from_api_item(item)
        self.assertEqual(song.played, '')

    def test_topic_channel_is_dropped(self):
        item = api_item(artists=[{'name': 'Some Artist - Topic'}])
        self.assertIsNone(HistorySong.from_api_item(item))

    def test_missing_artists_is_dropped(self):
        item = api_item()
        del item['artists']
        self.assertIsNone(HistorySong.from_api_item(item))

    def test_empty_artists_is_dropped(self):
        self.assertIsNone(HistorySong.from_api_item(api_item(artists=[])))

    def test_artist_without_name_is_dropped(self):
        self.assertIsNone(HistorySong.from_api_item(api_item(artists=[{'id': 'UCxxx'}])))

    def test_missing_title_is_dropped(self):
        item = api_item()
        del item['title']
        self.assertIsNone(HistorySong.from_api_item(item))

    def test_blank_title_is_dropped(self):
        self.assertIsNone(HistorySong.from_api_item(api_item(title='')))


if __name__ == '__main__':
    unittest.main()
