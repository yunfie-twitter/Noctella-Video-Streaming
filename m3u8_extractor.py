import sys
import subprocess
import argparse
import json
from yt_dlp import YoutubeDL
from urllib.parse import urlparse, parse_qs
from typing import List, Dict, Optional, Tuple


class M3U8Extractor:
    YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "music.youtube.com")

    def __init__(self, max_workers: int = 5, video: bool = True, quality: Optional[str] = None, abr: Optional[str] = None):
        self.max_workers = max_workers
        self.video = video
        self.quality = quality
        self.abr = abr
        self.ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'no_warnings': True,
        }
        self._cache: Dict[str, Optional[str]] = {}

    def normalize_url(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if "music.youtube.com" in hostname:
            qs = parse_qs(parsed.query)
            video_ids = qs.get("v")
            if video_ids and video_ids[0]:
                return f"https://www.youtube.com/watch?v={video_ids[0]}"
            return None

        if any(domain in hostname for domain in self.YOUTUBE_DOMAINS):
            return url

        return None

    def extract_video_audio_urls(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        normalized_url = self.normalize_url(url)
        if not normalized_url:
            print(f"[SKIP] Not a YouTube URL: {url}")
            return None, None

        with YoutubeDL(self.ydl_opts) as ydl:
            try:
                info = ydl.extract_info(normalized_url, download=False)
                video_url, audio_url = None, None

                for fmt in info.get("formats", []):
                    if fmt.get("protocol") not in ["m3u8_native", "m3u8_dash"]:
                        continue

                    vcodec = fmt.get("vcodec")
                    acodec = fmt.get("acodec")
                    f_url = fmt.get("url")

                    # 映像＋音声つき（最優先）
                    if vcodec != "none" and acodec != "none":
                        return f_url, None

                    # 映像のみ
                    if vcodec != "none" and acodec == "none":
                        if self.video:
                            if self.quality and fmt.get("height"):
                                if f"{fmt.get('height')}p" == self.quality:
                                    video_url = f_url
                            elif not self.quality:
                                video_url = f_url

                    # 音声のみ
                    if vcodec == "none" and acodec != "none":
                        if not self.video:
                            if self.abr and fmt.get("abr"):
                                if f"{int(fmt.get('abr'))}k" == self.abr:
                                    audio_url = f_url
                            elif not self.abr:
                                audio_url = f_url
                        elif self.video and not audio_url:
                            # fallback
                            audio_url = f_url

                return video_url, audio_url

            except Exception as e:
                print(f"[ERROR] yt-dlp extraction failed for {url}: {e}")
                return None, None

    def merge_streams_with_ffmpeg(self, video_url: str, audio_url: str, output_path: str = "output.mp4"):
        print(f"[FFMPEG] Merging video and audio to {output_path} ...")
        cmd = [
            "ffmpeg", "-y",
            "-i", video_url,
            "-i", audio_url,
            "-c", "copy",
            output_path
        ]
        try:
            subprocess.run(cmd, check=True)
            print(f"[FFMPEG] Merge completed: {output_path}")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] ffmpeg failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+", help="YouTube video URLs")
    parser.add_argument("--audio-only", action="store_true", help="Only extract audio stream")
    parser.add_argument("--quality", help="Video quality (e.g. 720p)")
    parser.add_argument("--ab", help="Audio bitrate (e.g. 128k)")
    parser.add_argument("--merge", action="store_true", help="Merge video and audio with ffmpeg if separate")
    parser.add_argument("--json", action="store_true", help="Output extraction result as JSON")
    args = parser.parse_args()

    extractor = M3U8Extractor(
        max_workers=3,
        video=not args.audio_only,
        quality=args.quality,
        abr=args.ab
    )

    results = {}

    for url in args.urls:
        video_url, audio_url = extractor.extract_video_audio_urls(url)
        if args.merge:
            if video_url and audio_url:
                extractor.merge_streams_with_ffmpeg(video_url, audio_url)
        results[url] = {
            "video": video_url,
            "audio": audio_url,
        }

    if args.json:
        # JSONとして標準出力に出すだけで終了
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        # 通常の標準出力
        for url, streams in results.items():
            print(f"\n[INFO] URL: {url}")
            video_url = streams.get("video")
            audio_url = streams.get("audio")

            if args.audio_only:
                if audio_url:
                    print(f"[OK] Retrieved audio .m3u8 URL:\n{audio_url}")
                else:
                    print("[FAIL] Failed to retrieve audio stream.")
            elif args.merge:
                if video_url and audio_url:
                    print(f"[INFO] Retrieved separate streams:")
                    print(f"  Video: {video_url}")
                    print(f"  Audio: {audio_url}")
                elif video_url:
                    print(f"[OK] Retrieved single video+audio stream:\n{video_url}")
                else:
                    print("[FAIL] Failed to retrieve streams.")
            else:
                if video_url:
                    print(f"[INFO] Video stream:\n{video_url}")
                else:
                    print("[WARN] No video stream found.")
                if audio_url:
                    print(f"[INFO] Audio stream:\n{audio_url}")
                else:
                    print("[WARN] No audio stream found.")


if __name__ == "__main__":
    main()
