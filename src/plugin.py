import asyncio
import logging
import platform
import random
import re
import sys
import webbrowser
from functools import partial
from http.cookies import SimpleCookie, Morsel
from typing import Dict

from galaxy.api.plugin import Plugin, create_and_run_plugin
from galaxy.api.types import Achievement, Authentication, Cookie, FriendInfo, Game, GameTime, LicenseInfo, NextStep
from galaxy.api.errors import (
    AuthenticationRequired, UnknownBackendResponse, AccessDenied, InvalidCredentials, UnknownError
)
from galaxy.api.consts import Platform, LicenseType
from galaxy.api.jsonrpc import InvalidParams
from backend import SteamHttpClient, AuthenticatedHttpClient
from local_games import local_games_list, get_state_changes
from registry_monitor import get_steam_registry_monitor
from uri_scheme_handler import is_uri_handler_installed
from version import __version__
from cache import Cache

def is_windows():
    return platform.system().lower() == "windows"


LOGIN_URI = r"https://steamcommunity.com/login/home/?goto="
JS_PERSISTENT_LOGIN = r"document.getElementById('remember_login').checked = true;"
END_URI_REGEX = r"^https://steamcommunity.com/(profiles|id)/.*"

AUTH_PARAMS = {
    "window_title": "Login to Steam",
    "window_width": 640,
    "window_height": 462 if is_windows() else 429,
    "start_uri": LOGIN_URI,
    "end_uri_regex": END_URI_REGEX
}

def morsels_to_dicts(morsels):
    cookies = []
    for morsel in morsels:
        cookie = {
            "name": morsel.key,
            "value": morsel.value,
            "domain": morsel["domain"],
            "path": morsel["path"]
        }
        cookies.append(cookie)
    return cookies

def dicts_to_morsels(cookies):
    morsels = []
    for cookie in cookies:
        name = cookie["name"]
        value = cookie["value"]
        m = Morsel()
        m.set(name, value, value)
        m["domain"] = cookie.get("domain", "")
        m["path"] = cookie.get("path", "")
        morsels.append(m)
    return morsels

def parse_stored_cookies(cookies):
    if isinstance(cookies, dict):
        cookies = [{"name": key, "value": value} for key, value in cookies.items()]
    return dicts_to_morsels(cookies)

class SteamPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(Platform.Steam, __version__, reader, writer, token)
        self._steam_id = None
        self._regmon = get_steam_registry_monitor()
        self._local_games_cache = local_games_list()
        self._http_client = AuthenticatedHttpClient()
        self._client = SteamHttpClient(self._http_client)
        self._achievements_cache = Cache()

    def _store_cookies(self, cookies):
        credentials = {
            "cookies": morsels_to_dicts(cookies)
        }
        self.store_credentials(credentials)

    @staticmethod
    def _create_two_factor_fake_cookie():
        return Cookie(
            # random SteamID with proper "instance", "type" and "universe" fields
            # (encoded in most significant bits)
            name="steamMachineAuth{}".format(random.randint(1, 2 ** 32 - 1) + 0x01100001 * 2 ** 32),
            # 40-bit random string encoded as hex
            value=hex(random.getrandbits(20 * 8))[2:].upper()
        )

    def shutdown(self):
        asyncio.create_task(self._http_client.close())
        self._regmon.close()

    async def _do_auth(self, morsels):
        cookies = [(morsel.key, morsel) for morsel in morsels]

        self._http_client.update_cookies(cookies)
        self._http_client.set_cookies_updated_callback(self._store_cookies)
        self._force_utc()

        try:
            profile_url = await self._client.get_profile()
        except UnknownBackendResponse:
            raise InvalidCredentials()

        try:
            self._steam_id, login = await self._client.get_profile_data(profile_url)
        except AccessDenied:
            raise InvalidCredentials()

        self._http_client.set_auth_lost_callback(self.lost_authentication)

        return Authentication(self._steam_id, login)

    def _force_utc(self):
        cookies = SimpleCookie()
        cookies["timezoneOffset"] = "0,0"
        morsel = cookies["timezoneOffset"]
        morsel["domain"] = "steamcommunity.com"
        # override encoding (steam does not fallow RFC 6265)
        morsel.set("timezoneOffset", "0,0", "0,0")
        self._http_client.update_cookies(cookies)

    async def authenticate(self, stored_credentials=None):
        if not stored_credentials:
            return NextStep(
                "web_session",
                AUTH_PARAMS,
                [self._create_two_factor_fake_cookie()],
                {re.escape(LOGIN_URI): [JS_PERSISTENT_LOGIN]}
            )

        cookies = stored_credentials.get("cookies", [])
        morsels = parse_stored_cookies(cookies)
        return await self._do_auth(morsels)

    async def pass_login_credentials(self, step, credentials, cookies):
        try:
            morsels = dicts_to_morsels(cookies)
        except Exception:
            raise InvalidParams()

        auth_info = await self._do_auth(morsels)
        self._store_cookies(morsels)
        return auth_info

    async def get_owned_games(self):
        if self._steam_id is None:
            raise AuthenticationRequired()

        games = await self._client.get_games(self._steam_id)

        owned_games = []

        try:
            for game in games:
                owned_games.append(
                    Game(
                        str(game["appid"]),
                        game["name"],
                        [],
                        LicenseInfo(LicenseType.SinglePurchase, None)
                    )
                )
        except (KeyError, ValueError):
            logging.exception("Can not parse backend response")
            raise UnknownBackendResponse()

        return owned_games

    async def get_game_times(self):
        """"Left for automatic feature detection"""
        if self._steam_id is None:
            raise AuthenticationRequired()
        game_times = await self._get_game_times_dict()
        return list(game_times.values())

    async def start_game_times_import(self, game_ids):
        if self._steam_id is None:
            raise AuthenticationRequired()

        await super().start_game_times_import(game_ids)

    async def import_game_times(self, game_ids):
        remaining_game_ids = set(game_ids)
        try:
            game_times = await self._get_game_times_dict()
            for game_id in game_ids:
                game_time = game_times.get(game_id)
                if game_time is None:
                    self.game_time_import_failure(game_id, UnknownError())
                else:
                    self.game_time_import_success(game_time)
                remaining_game_ids.remove(game_id)
        except Exception as error:
            logging.exception("Fail to import game times")
            for game_id in remaining_game_ids:
                self.game_time_import_failure(game_id, error)

    async def _get_game_times_dict(self) -> Dict[str, GameTime]:
        games = await self._client.get_games(self._steam_id)

        game_times = {}

        try:
            for game in games:
                last_played = game.get("last_played")
                if last_played is None:
                    continue
                game_id = str(game["appid"])
                game_times[game_id] = GameTime(
                    game_id,
                    int(float(game.get("hours_forever", "0").replace(",", "")) * 60),
                    last_played
                )
        except (KeyError, ValueError):
            logging.exception("Can not parse backend response")
            raise UnknownBackendResponse()

        return game_times

    async def get_unlocked_achievements(self, game_id):
        if self._steam_id is None:
            raise AuthenticationRequired()

        return await self._get_achievements(game_id)

    async def start_achievements_import(self, game_ids):
        if self._steam_id is None:
            raise AuthenticationRequired()

        await super().start_achievements_import(game_ids)

    async def import_games_achievements(self, game_ids):
        remaining_game_ids = set(game_ids)
        try:
            game_times = await self._get_game_times_dict()

            tasks = []
            for game_id in game_ids:
                game_time = game_times.get(game_id)
                if game_time is None or game_time.time_played == 0:
                    # no game time - assume empty achievements
                    self.game_achievements_import_success(game_id, [])
                    continue

                timestamp = game_time.last_played_time
                achievements = self._achievements_cache.get(game_id, timestamp)

                if achievements is not None:
                    # return from cache
                    self.game_achievements_import_success(game_id, achievements)
                    continue

                # fetch from backend and update cache
                tasks.append(asyncio.create_task(self._import_game_achievements(game_id, timestamp)))

            await asyncio.gather(*tasks)
        except Exception as error:
            logging.exception("Failed to retrieve game times")
            for game_id in remaining_game_ids:
                self.game_achievements_import_failure(game_id, error)

    async def _import_game_achievements(self, game_id, timestamp):
        """For fetching single game achievements"""
        try:
            achievements = await self._get_achievements(game_id)
            self.game_achievements_import_success(game_id, achievements)
            self._achievements_cache.update(game_id, achievements, timestamp)
        except Exception as error:
            self.game_achievements_import_failure(game_id, error)

    async def _get_achievements(self, game_id):
        achievements = await self._client.get_achievements(self._steam_id, game_id)
        return [Achievement(unlock_time, None, name) for unlock_time, name in achievements]

    async def get_friends(self):
        if self._steam_id is None:
            raise AuthenticationRequired()

        return [
            FriendInfo(user_id=user_id, user_name=user_name)
            for user_id, user_name in (await self._client.get_friends(self._steam_id)).items()
        ]

    def tick(self):

        async def _update_local_games():
            loop = asyncio.get_running_loop()
            new_list = await loop.run_in_executor(None, local_games_list)
            notify_list = get_state_changes(self._local_games_cache, new_list)
            self._local_games_cache = new_list
            for local_game_notify in notify_list:
                self.update_local_game_status(local_game_notify)

        if self._regmon.check_if_updated():
            asyncio.create_task(_update_local_games())

    async def get_local_games(self):
        return self._local_games_cache

    @staticmethod
    async def _open_uri(uri):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(webbrowser.open, uri))

    @staticmethod
    async def _steam_command(command, game_id):
        if is_uri_handler_installed("steam"):
            await SteamPlugin._open_uri("steam://{}/{}".format(command, game_id))
        else:
            await SteamPlugin._open_uri("https://store.steampowered.com/about/")

    async def launch_game(self, game_id):
        await SteamPlugin._steam_command("launch", game_id)

    async def install_game(self, game_id):
        await SteamPlugin._steam_command("install", game_id)

    async def uninstall_game(self, game_id):
        await SteamPlugin._steam_command("uninstall", game_id)


def main():
    create_and_run_plugin(SteamPlugin, sys.argv)


if __name__ == "__main__":
    main()
