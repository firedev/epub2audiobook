#!/usr/bin/env python3
"""Convert EPUB books to audiobooks using Edge TTS."""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import edge_tts
from pydub import AudioSegment

CHUNK_SIZE = 4000


def parse_epub(epub_path):
    """Extract chapters from epub in spine order. Returns list of (title, text) tuples."""
    book = epub.read_epub(epub_path, options={"ignore_ncx": True})
    chapters = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        if not text or len(text.strip()) < 50:
            continue
        title = None
        heading = soup.find(["h1", "h2", "h3"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = item.get_name().split("/")[-1].replace(".xhtml", "").replace(".html", "")
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


async def tts_chunk(text, voice, rate, output_path):
    """Convert a single text chunk to MP3 via Edge TTS."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


async def convert_chapter(chapter_num, title, text, voice, rate, output_dir):
    """Convert a full chapter to MP3, chunking if needed."""
    safe_title = re.sub(r"[^\w\s-]", "", title)[:50].strip()
    filename = f"{chapter_num:02d}_{safe_title}.mp3"
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
        chunk_segments = []
        tmp_dir = output_dir / "_tmp"
        tmp_dir.mkdir(exist_ok=True)
        for i, chunk in enumerate(chunks):
            tmp_path = tmp_dir / f"ch{chapter_num:02d}_chunk{i:03d}.mp3"
            await tts_chunk(chunk, voice, rate, str(tmp_path))
            chunk_segments.append(tmp_path)
        combined = AudioSegment.empty()
        for seg_path in chunk_segments:
            combined += AudioSegment.from_mp3(str(seg_path))
        combined.export(str(chapter_path), format="mp3")
        for seg_path in chunk_segments:
            seg_path.unlink()
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
    chapter_paths = []
    for i, (title, text) in enumerate(chapters, 1):
        print(f"[{i}/{len(chapters)}] {title}")
        path = await convert_chapter(i, title, text, args.voice, args.rate, output_dir)
        if path:
            chapter_paths.append(path)
    if not args.no_merge and len(chapter_paths) > 1:
        print(f"\nMerging {len(chapter_paths)} chapters...")
        combined = AudioSegment.empty()
        for path in chapter_paths:
            combined += AudioSegment.from_mp3(str(path))
        merged_path = output_dir / f"{book_name}_complete.mp3"
        combined.export(str(merged_path), format="mp3")
        print(f"Complete audiobook: {merged_path}")
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
