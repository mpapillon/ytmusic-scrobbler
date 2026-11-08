"""Unit tests for models.Track: the read-only view over a raw ytmusicapi
get_history() entry, and the rules deciding what counts as a regular track
(no podcasts/non-music entries, no auto-generated ' - Topic' channels)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytscrobbler.models import Track


def api_item(**overrides):
    item = {
        "videoId": "vid-1",
        "title": "Song",
        "artists": [{"name": "Artist", "id": "UCxxx"}],
        "album": {"name": "Album", "id": "MPREb_xxx"},
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
        "played": "Today",
    }
    item.update(overrides)
    return item


class TestTrackProperties(unittest.TestCase):
    def test_full_item(self):
        track = Track(api_item())
        self.assertEqual(track.video_id, "vid-1")
        self.assertEqual(track.title, "Song")
        self.assertEqual(track.artist, "Artist")
        self.assertEqual(track.album.name, "Album")
        self.assertEqual(track.video_type, "MUSIC_VIDEO_TYPE_ATV")
        self.assertEqual(track.played, "Today")

    def test_multi_artists_exposes_all_and_first_as_artist(self):
        track = Track(api_item(artists=[{"name": "First"}, {"name": "Second"}]))
        self.assertEqual([a.name for a in track.artists], ["First", "Second"])
        self.assertEqual(track.artist, "First")

    def test_missing_fields_fall_back_to_empty_values(self):
        track = Track({})
        self.assertEqual(track.video_id, "")
        self.assertEqual(track.title, "")
        self.assertEqual(track.artists, [])
        self.assertEqual(track.album.name, "")
        self.assertEqual(track.video_type, "")
        self.assertEqual(track.played, "")

    def test_album_uses_its_own_name(self):
        track = Track(api_item(album=None))
        self.assertEqual(track.album.name, "")

    def test_artist_and_album_ids(self):
        track = Track(api_item())
        self.assertEqual(track.artists[0].id, "UCxxx")
        self.assertEqual(track.album.id, "MPREb_xxx")


class TestTrackIsRegularTrack(unittest.TestCase):
    def test_music_video_type_is_regular(self):
        self.assertTrue(Track(api_item()).is_regular_track)

    def test_omv_video_type_is_regular(self):
        track = Track(api_item(videoType="MUSIC_VIDEO_TYPE_OMV"))
        self.assertTrue(track.is_regular_track)

    def test_absent_video_type_is_not_regular(self):
        """Podcasts, uploaded videos and other non-music entries have no
        MUSIC_VIDEO_TYPE_* videoType and must be filtered out."""
        item = api_item()
        del item["videoType"]
        self.assertFalse(Track(item).is_regular_track)

    def test_other_music_video_types_are_regular(self):
        track = Track(api_item(videoType="MUSIC_VIDEO_TYPE_UGC"))
        self.assertTrue(track.is_regular_track)

    def test_topic_channel_is_not_regular(self):
        track = Track(api_item(artists=[{"name": "Some Artist - Topic"}]))
        self.assertFalse(track.is_regular_track)

    def test_missing_artists_is_regular(self):
        """Track is a dumb view; filtering on missing artists happens upstream
        if needed. Only the topic-channel and videoType rules live here."""
        item = api_item()
        del item["artists"]
        self.assertTrue(Track(item).is_regular_track)


class TestTrackWasPlayedToday(unittest.TestCase):
    def test_today_is_played_today(self):
        self.assertTrue(Track(api_item(played="Today")).was_played_today)
        self.assertTrue(Track(api_item(played="today")).was_played_today)

    def test_other_days_are_not_played_today(self):
        self.assertFalse(Track(api_item(played="Yesterday")).was_played_today)

    def test_missing_played_is_not_played_today(self):
        item = api_item()
        del item["played"]
        self.assertFalse(Track(item).was_played_today)


if __name__ == "__main__":
    unittest.main()
