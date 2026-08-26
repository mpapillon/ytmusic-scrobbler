# Last.fm Syncing Tool
# Lean, fast, and functional
import hashlib
import os
import time
import xml.etree.ElementTree as ET

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

api_head = 'http://ws.audioscrobbler.com/2.0/'
secret = os.environ['LAST_FM_API_SECRET']


def get_session(user_token: str) -> str:
    params = {
        'api_key': os.environ['LAST_FM_API'],
        'method': 'auth.getSession',
        'token': user_token
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = requests.post(api_head, params)
    return apiResp.text


def get_token() -> str:
    params = {
        'api_key': os.environ['LAST_FM_API'],
        'method': 'auth.getToken',
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = requests.post(api_head, params)
    root = ET.fromstring(apiResp.text)
    if (token := root.find("token")) is not None and token.text:
        return token.text
    error = root.find("error")
    message = error.text.strip() if error is not None and error.text else apiResp.text
    raise Exception(f"Last.fm auth.getToken failed: {message}")


def nowPlaying(song_name: str, artist_name: str, session_key: str) -> str:
    params = {
        'method': 'track.updateNowPlaying',
        'api_key': os.environ['LAST_FM_API'],
        'track': song_name,
        'artist': artist_name,
        'sk': session_key
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = requests.post(api_head, params)
    return apiResp.text


def scrobble(song_name: str, artist_name: str, album_name: str, session_key: str, timestamp: str | None = None) -> str:
    # Currently this sort of cheats the timestamp protocol
    params = {
        'method': 'track.scrobble',
        'api_key': os.environ['LAST_FM_API'],
        'timestamp': timestamp if timestamp else str(int(time.time() - 30)),
        'track': song_name,
        'artist': artist_name,
        'album': album_name,
        'sk': session_key
    }
    requestHash = hashRequest(params, secret)
    params['api_sig'] = requestHash
    apiResp = requests.post(api_head, params)
    return apiResp.text


def hashRequest(obj: dict[str, str], secretKey: str) -> str:
    string = ''
    items = list(obj.keys())
    items.sort()
    for i in items:
        string += i
        string += obj[i]
    string += secretKey
    stringToHash = string.encode('utf8')
    requestHash = hashlib.md5(stringToHash).hexdigest()
    return requestHash
