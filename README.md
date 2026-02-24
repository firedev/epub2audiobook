# epub2audiobook

Convert EPUB books to audiobooks using Microsoft Edge TTS. Free, no API key required.

Designed for Russian-language books but works with any language supported by Edge TTS.

## Install

### macOS

```bash
brew install python ffmpeg
git clone https://github.com/firedev/epub2audiobook.git
cd epub2audiobook
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Linux (Debian/Ubuntu)

```bash
sudo apt install python3 python3-venv ffmpeg
git clone https://github.com/firedev/epub2audiobook.git
cd epub2audiobook
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Russian male voice (default)
python epub2audio.py book.epub

# Russian female voice
python epub2audio.py book.epub --voice ru-RU-SvetlanaNeural

# Custom output directory
python epub2audio.py book.epub --output ./my_audiobook

# Faster speech
python epub2audio.py book.epub --rate "+15%"

# Chapter files only, no combined file
python epub2audio.py book.epub --no-merge
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `epub_file` | required | Path to .epub file |
| `--voice` | `ru-RU-DmitryNeural` | Edge TTS voice ID |
| `--output` | `./output` | Output directory |
| `--no-merge` | false | Skip creating combined MP3 |
| `--rate` | `+0%` | Speech rate (`+10%`, `-5%`, etc.) |

## Output

```
output/
  book-name/
    01_Chapter_One.mp3
    02_Chapter_Two.mp3
    ...
    book-name_complete.mp3    # all chapters merged
```

Re-running is safe — existing chapter files are skipped.

## Voices

Russian voices:
- `ru-RU-DmitryNeural` — male, clear, neutral
- `ru-RU-SvetlanaNeural` — female, clear, neutral

List all available voices:

```bash
edge-tts --list-voices | grep ru-RU
```

Works with any Edge TTS voice. For English: `en-US-GuyNeural`, `en-US-JennyNeural`, etc.

## Requirements

- Python 3.10+
- ffmpeg (for concatenating chapter MP3s)

## License

MIT
