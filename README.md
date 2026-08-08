# NoAccessBeGoneLite

Shows channels you don't have access to and reveals their names — when they're
known in the community database.

Discord obfuscates channel names for channels you can't view. But the database in
this repo (see `names.jsonl`) contains names contributed by people who *do* have
access to those channels. NoAccessBeGoneLite downloads the database and displays
names for any hidden channel you encounter that's already in it.

- Hidden channels appear in the sidebar (dimmed) with a lock icon
- Names come from `names.jsonl` — the same file you see in this repo
- No requests to Discord APIs beyond what the normal client already does
- Database refreshes automatically (default: hourly)

## Install

```bash
# macOS / Linux
./install.sh

# Windows (cmd)
install.bat
```

The script clones Vencord, drops the plugin in, builds it, and prints the exact
steps to load it (Discord desktop via `pnpm inject`, or point Vesktop's "Vencord
Location" at the dist folder). Then enable **NoAccessBeGoneLite** in the Vencord
plugins list and restart.

## Contributing names

Install the full NoAccessBeGone plugin (private) and enable name sharing in its
settings — your discovered names get uploaded to the database periodically. Or
open a PR adding lines to `names.jsonl`:

```
{"g":"<guild id>","c":"<channel id>","n":"<channel name>"}
```

## License

GPL-3.0, same as Vencord (parts of the webpack patch logic are adapted from
Vencord's ShowHiddenChannels).
