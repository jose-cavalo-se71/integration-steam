from galaxy.api.types import LocalGame, LocalGameState

import logging
import platform

# Windows registry implementation
if platform.system() == "Windows":
    import winreg

    def registry_apps_as_dict():
        try:
            apps = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\Apps")
        except OSError:
            logging.exception("Failed to read Steam registry")
            return {}

        apps_dict = dict()
        sub_key_index = 0

        while True:
            try:
                sub_key_name = winreg.EnumKey(apps, sub_key_index)
                sub_key_dict = dict()
                with winreg.OpenKey(apps, sub_key_name) as sub_key:
                    value_index = 0
                    while True:
                        try:
                            v = winreg.EnumValue(sub_key, value_index)
                            sub_key_dict[v[0]] = v[1]
                            value_index += 1
                        except OSError:
                            break
                    winreg.CloseKey(sub_key)
                apps_dict[sub_key_name] = sub_key_dict
                sub_key_index += 1
            except OSError:
                logging.exception("Failed to parse Steam registry")
                break

        winreg.CloseKey(apps)

        return apps_dict

# MacOS "registry" implementation (registry.vdf file)
elif platform.system().lower() == "darwin":
    import os
    import vdf

    class CaseInsensitiveDict(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key.lower(), value)

        def __getitem__(self, key):
            return super().__getitem__(key.lower())

    def registry_apps_as_dict():
        try:
            registry = vdf.load(
                open(os.path.expanduser("~/Library/Application Support/Steam/registry.vdf")),
                mapper=CaseInsensitiveDict
            )
        except OSError:
            logging.exception("Failed to read Steam registry")
            return {}

        try:
            return registry["Registry"]["HKCU"]["Software"]["Valve"]["Steam"]["Apps"]
        except KeyError:
            logging.exception("Failed to parse Steam registry")
            return {}

# fallback for other systems
else:
    def registry_apps_as_dict():
        return {}

def registry_app_dict_to_local_games_list(app_dict):
    games = []
    for game, game_data in app_dict.items():
        state = LocalGameState.None_
        for k, v in game_data.items():
            if k.lower() == "running" and str(v) == "1":
                state |= LocalGameState.Running
            if k.lower() == "installed" and str(v) == "1":
                state |= LocalGameState.Installed
        games.append(LocalGame(game, state))

    logging.debug("Local game list: {}".format(games))
    return games

def local_games_list():
    return registry_app_dict_to_local_games_list(registry_apps_as_dict())

def get_state_changes(old_list, new_list):
    old_dict = {x.game_id: x.local_game_state for x in old_list}
    new_dict = {x.game_id: x.local_game_state for x in new_list}
    result = []
    # removed games
    result.extend(LocalGame(id, LocalGameState.None_) for id in old_dict.keys() - new_dict.keys())
    # added games
    result.extend(local_game for local_game in new_list if local_game.game_id in new_dict.keys() - old_dict.keys())
    # state changed
    result.extend(LocalGame(id, new_dict[id]) for id in new_dict.keys() & old_dict.keys() if new_dict[id] != old_dict[id])
    return result
