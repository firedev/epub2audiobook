#!/usr/bin/env python3
"""Convert EPUB books to audiobooks using Edge TTS."""

import argparse
import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import edge_tts

CHUNK_SIZE = 4000


def parse_epub(epub_path):
    """Extract chapters from epub in spine order. Returns list of (title, text) tuples."""
    book = epub.read_epub(epub_path, options={"ignore_ncx": True})
    chapters = []
    for spine_id, _ in book.spine:
        item = book.get_item_with_id(spine_id)
        if not item:
            continue
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        if not text or len(text.strip()) < 50:
            continue
        heading = soup.find(["h1", "h2", "h3"])
        title = heading.get_text(strip=True) if heading else None
        if not title:
            title = text.strip().split("\n")[0][:80]
        chapters.append((title, text))
    return chapters


def chunk_text(text, max_size=CHUNK_SIZE):
    """Split text into chunks at paragraph boundaries, respecting max_size."""
    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 1 > max_size:
            if current:
                chunks.append(current)
            if len(para) > max_size:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sentence in sentences:
                    if len(current) + len(sentence) + 1 > max_size:
                        if current:
                            chunks.append(current)
                        current = sentence
                    else:
                        current = f"{current} {sentence}".strip()
            else:
                current = para
        else:
            current = f"{current}\n{para}".strip()
    if current:
        chunks.append(current)
    return chunks


def concat_mp3s(input_paths, output_path):
    """Concatenate MP3 files using ffmpeg."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in input_paths:
            escaped = str(Path(path).resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        list_file = f.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(Path(output_path).resolve())],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"  ffmpeg error: {result.stderr.decode(errors='replace')[-500:]}")
            result.check_returncode()
    finally:
        Path(list_file).unlink()


async def tts_chunk(text, voice, rate, output_path):
    """Convert a single text chunk to MP3 via Edge TTS."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def pad_width(total):
    """Return the number of digits needed to represent total."""
    return len(str(total))


async def convert_chapter(chapter_num, title, text, voice, rate, output_dir, chapter_pad):
    """Convert a full chapter to MP3, chunking if needed."""
    safe_title = re.sub(r"[^\w\s-]", "", title)[:50].strip()
    num = str(chapter_num).zfill(chapter_pad)
    filename = f"{num}_{safe_title}.mp3" if safe_title else f"{num}.mp3"
    chapter_path = output_dir / filename
    if chapter_path.exists():
        print(f"  Skipping (exists): {filename}")
        return chapter_path
    chunks = chunk_text(text)
    if not chunks:
        return None
    if len(chunks) == 1:
        await tts_chunk(chunks[0], voice, rate, str(chapter_path))
    else:
        chunk_pad = pad_width(len(chunks))
        tmp_dir = output_dir / "_tmp"
        tmp_dir.mkdir(exist_ok=True)
        chunk_paths = []
        for i, chunk in enumerate(chunks):
            tmp_path = tmp_dir / f"ch{str(chapter_num).zfill(chapter_pad)}_chunk{str(i).zfill(chunk_pad)}.mp3"
            await tts_chunk(chunk, voice, rate, str(tmp_path))
            chunk_paths.append(tmp_path)
        concat_mp3s(chunk_paths, chapter_path)
        for p in chunk_paths:
            p.unlink()
        if not any(tmp_dir.iterdir()):
            tmp_dir.rmdir()
    return chapter_path


async def main():
    parser = argparse.ArgumentParser(description="Convert EPUB to audiobook using Edge TTS")
    parser.add_argument("epub_file", help="Path to .epub file")
    parser.add_argument("--voice", default="ru-RU-DmitryNeural", help="Edge TTS voice ID")
    parser.add_argument("--output", default="./output", help="Output directory")
    parser.add_argument("--no-merge", action="store_true", help="Skip creating combined MP3")
    parser.add_argument("--rate", default="+0%", help="Speech rate adjustment (e.g. +10%%, -5%%)")
    args = parser.parse_args()
    epub_path = Path(args.epub_file)
    if not epub_path.exists():
        print(f"Error: file not found: {epub_path}")
        sys.exit(1)
    book_name = epub_path.stem
    output_dir = Path(args.output) / book_name
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Parsing: {epub_path.name}")
    chapters = parse_epub(str(epub_path))
    if not chapters:
        print("Error: no chapters found in epub")
        sys.exit(1)
    print(f"Found {len(chapters)} chapters")
    print(f"Voice: {args.voice} | Rate: {args.rate}")
    print(f"Output: {output_dir}\n")
    chapter_pad = pad_width(len(chapters))
    chapter_paths = []
    for i, (title, text) in enumerate(chapters, 1):
        print(f"[{i}/{len(chapters)}] {title}")
        path = await convert_chapter(i, title, text, args.voice, args.rate, output_dir, chapter_pad)
        if path:
            chapter_paths.append(path)
    if not args.no_merge and len(chapter_paths) > 1:
        print(f"\nMerging {len(chapter_paths)} chapters...")
        merged_path = output_dir / f"{book_name}_complete.mp3"
        concat_mp3s(chapter_paths, merged_path)
        print(f"Complete audiobook: {merged_path}")
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
