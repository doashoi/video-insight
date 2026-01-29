import os
import sys
import subprocess
import gc
import re
import math
import shutil
import traceback
import json
import requests
import time
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Union, Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import config

logger = logging.getLogger("VideoProcessor")

class VideoAnalyzer:
    def __init__(self):
        """使用配置中的路径初始化 VideoAnalyzer。"""
        self.ffmpeg_exe = config.FFMPEG_PATH
        self.api_key = config.DASHSCOPE_API_KEY
        self.last_error = None  # 用于记录最近一次发生的具体错误原因
        
        # 注册 FFmpeg 路径
        ffmpeg_dir = os.path.dirname(str(self.ffmpeg_exe))
        if ffmpeg_dir and ffmpeg_dir not in os.environ["PATH"]:
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]
            logger.info(f"FFmpeg 路径已注册: {ffmpeg_dir}")

    def release_model(self):
        """释放资源。"""
        gc.collect()

    def extract_audio_track(self, video_path: str, audio_path: str) -> bool:
        """从视频提取音频 (16k, mono, mp3 format for smaller size)。"""
        # 使用 MP3 格式大幅减小文件体积，避免 Base64 编码后超过 API 限制
        # -ar 16000: 采样率 16k (ASR 标准)
        # -ac 1: 单声道
        # -b:a 32k: 比特率 32k (语音足够，且文件极小)
        cmd = [
            str(self.ffmpeg_exe), "-y", "-i", video_path,
            "-vn", "-c:a", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "32k",
            "-f", "mp3", audio_path, "-loglevel", "error"
        ]
        try:
            logger.info(f"正在提取音频 (MP3): {Path(video_path).name} -> {Path(audio_path).name}")
            # 使用 subprocess.run 时捕获 stderr 以便打印更详细的错误
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg 提取音频失败 (退出码 {result.returncode}): {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"音频提取发生异常: {e}")
            return False

    def _get_video_duration_s(self, video_path: str) -> float:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return 0.0

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        if fps and fps > 0:
            return float(total_frames / fps)
        return 0.0

    def _detect_speech_segments(self, audio_path: str, duration_s: float) -> List[Tuple[float, float]]:
        cmd = [
            str(self.ffmpeg_exe), "-y", "-i", audio_path,
            "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-", "-loglevel", "error"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True)
        except Exception:
            return []

        if result.returncode != 0 or not result.stdout:
            return []

        pcm = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
        if pcm.size < 16000:
            return []

        pre = np.empty_like(pcm)
        pre[0] = pcm[0]
        pre[1:] = pcm[1:] - 0.97 * pcm[:-1]

        sr = 16000.0
        frame_len = 320
        hop = 160
        n_frames = 1 + (len(pre) - frame_len) // hop
        if n_frames <= 0:
            return []

        rms = np.empty(n_frames, dtype=np.float32)
        for i in range(n_frames):
            start = i * hop
            frame = pre[start:start + frame_len]
            rms[i] = float(np.sqrt(np.mean(frame * frame) + 1e-12))

        if n_frames >= 5:
            kernel = np.ones(5, dtype=np.float32) / 5.0
            rms = np.convolve(rms, kernel, mode="same")

        med = float(np.median(rms))
        mad = float(np.median(np.abs(rms - med)))
        if mad > 1e-6:
            thr = med + 3.0 * mad
        else:
            thr = max(med * 1.8, 1e-4)

        speech_mask = rms > thr

        segments = []
        in_speech = False
        start_idx = 0
        silence_count = 0
        end_silence_frames = 5

        for i, is_speech in enumerate(speech_mask):
            if is_speech:
                if not in_speech:
                    in_speech = True
                    start_idx = i
                silence_count = 0
            else:
                if in_speech:
                    silence_count += 1
                    if silence_count >= end_silence_frames:
                        end_idx = i - silence_count + 1
                        s = (start_idx * hop) / sr
                        e = (end_idx * hop + frame_len) / sr
                        segments.append((s, e))
                        in_speech = False
                        silence_count = 0

        if in_speech:
            s = (start_idx * hop) / sr
            e = len(pre) / sr
            segments.append((s, e))

        if not segments:
            total = len(pre) / sr
            if duration_s and duration_s > 0:
                total = min(total, duration_s)
            return [(0.0, round(total, 2))]

        merged = []
        min_speech = 0.6
        for start, end in segments:
            if end - start < min_speech:
                continue
            if not merged:
                merged.append([start, end])
                continue
            if start - merged[-1][1] <= 0.15:
                merged[-1][1] = end
            else:
                merged.append([start, end])

        if not merged:
            total = len(pre) / sr
            if duration_s and duration_s > 0:
                total = min(total, duration_s)
            return [(0.0, round(total, 2))]

        return [(round(s, 2), round(e, 2)) for s, e in merged]

    def _submit_asr_text(self, audio_path: str) -> Optional[str]:
        import base64
        self.last_error = None

        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            with open(audio_path, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode("utf-8")
            mime_type = "audio/mpeg" if audio_path.endswith(".mp3") else "audio/wav"

            payload = {
                "model": "qwen3-asr-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": f"data:{mime_type};base64,{audio_b64}"
                                }
                            }
                        ]
                    }
                ],
                "asr_options": {
                    "enable_timestamp": True,
                    "enable_word_timestamp": True
                }
            }

            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code != 200:
                self.last_error = f"ASR 接口返回错误 (HTTP {resp.status_code}): {resp.text}"
                return None

            res_data = resp.json()
            message = res_data.get("choices", [{}])[0].get("message", {})
            return (message.get("content") or "").strip()
        except Exception as e:
            self.last_error = f"ASR 提交任务发生未知异常: {str(e)}"
            return None

    def _split_text_into_timed_sentences(self, text: str, duration_s: float) -> List[Dict]:
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        if not cleaned or duration_s <= 0:
            return []

        parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;])\s*", cleaned) if p.strip()]
        if len(parts) > 1:
            fixed = []
            leading_join_chars = set("”\"’')）】》」』、,，.。:：;；!?！？")
            for p in parts:
                if fixed and p and p[0] in leading_join_chars:
                    fixed[-1] = (fixed[-1].rstrip() + p).strip()
                    continue
                if fixed and fixed[-1].count("“") > fixed[-1].count("”") and p.startswith("”"):
                    fixed[-1] = (fixed[-1].rstrip() + p).strip()
                    continue
                fixed.append(p)
            parts = fixed
        if len(parts) <= 1:
            return [{
                "start": 0,
                "end": int(duration_s * 1000),
                "text": cleaned,
                "words": []
            }]

        weights = [max(1, len(re.sub(r"\s+", "", p))) for p in parts]
        total_w = float(sum(weights))
        total_ms = int(duration_s * 1000)

        items = []
        cursor = 0
        for i, (p, w) in enumerate(zip(parts, weights)):
            if i == len(parts) - 1:
                end_ms = total_ms
            else:
                end_ms = cursor + int(round(total_ms * (w / total_w)))
            end_ms = max(end_ms, cursor + 200)
            items.append({
                "start": cursor,
                "end": end_ms,
                "text": p,
                "words": []
            })
            cursor = end_ms

        merged = []
        min_ms = 1200
        for item in items:
            if not merged:
                merged.append(item)
                continue
            if merged[-1]["end"] - merged[-1]["start"] < min_ms:
                merged[-1]["text"] = (merged[-1]["text"].rstrip() + " " + item["text"].lstrip()).strip()
                merged[-1]["end"] = item["end"]
            else:
                merged.append(item)

        if merged and merged[-1]["end"] != total_ms:
            merged[-1]["end"] = total_ms

        return merged

    def analyze_audio(self, video_path: str, output_dir: str) -> Optional[List[Dict]]:
        """调用阿里云 DashScope ASR 服务进行识别。"""
        temp_audio_dir = Path(output_dir) / "temp_audio"
        temp_audio_dir.mkdir(exist_ok=True)
        # 使用 .mp3 后缀
        audio_path = temp_audio_dir / "full_audio.mp3"
        
        if not self.extract_audio_track(video_path, str(audio_path)):
            # 清理目录
            try: shutil.rmtree(temp_audio_dir)
            except: pass
            return None

        print(f"[Analysis] 正在通过 DashScope 处理音频: {audio_path.name}")
        
        if not self.api_key:
            print("[Error] 未配置 DASHSCOPE_API_KEY，无法进行 ASR 识别")
            return None

        # 1. 语音识别 (使用 DashScope Base64 同步提交)
        try:
            # 提交 ASR 任务，传入音频路径以便内部处理 Base64 和大小检查
            asr_response = self._submit_asr_task(str(audio_path))
            if not asr_response:
                print("[Error] ASR 识别流程失败，未能获取有效响应")
                return None
            
            # 同步返回，直接获取结果
            result_data = asr_response
            print(f"[ASR] 成功收到 ASR 同步返回结果")
            
            # 2. 解析结果
            results = []
            output = result_data.get("output", {})
            
            # qwen-asr-flash 响应结构解析
                sentences = output.get("sentences", [])
                
                if not sentences:
                    # 尝试解析 output.results[0].sentences (部分模型结构)
                    res_list = output.get("results", [])
                    if res_list:
                        sentences = res_list[0].get("sentences", [])

                if not sentences:
                    full_text = (output.get("text") or "").strip()
                    duration_s = self._get_video_duration_s(video_path)
                    segments = self._detect_speech_segments(str(audio_path), duration_s)
                    if segments:
                        results = []
                        for idx, (seg_start, seg_end) in enumerate(segments):
                            seg_path = temp_audio_dir / f"seg_{idx:03d}.mp3"
                            cmd = [
                                str(self.ffmpeg_exe), "-y", "-i", str(audio_path),
                                "-ss", str(seg_start), "-to", str(seg_end),
                                "-c:a", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "32k",
                                "-f", "mp3", str(seg_path), "-loglevel", "error"
                            ]
                            try:
                                subprocess.run(cmd, check=True)
                            except Exception:
                                continue

                            seg_text = self._submit_asr_text(str(seg_path))
                            if not seg_text:
                                continue

                            item = {
                                "start": int(seg_start * 1000),
                                "end": int(seg_end * 1000),
                                "text": seg_text.strip(),
                                "words": []
                            }
                            logger.info(f"  [{seg_start:.2f}s - {seg_end:.2f}s]: {item['text']}")
                            results.append(item)

                        if results:
                            if len(results) == 1 and len(segments) == 1:
                                seg_duration_s = max(0.0, (results[0]["end"] - results[0]["start"]) / 1000.0)
                                refined = self._split_text_into_timed_sentences(results[0]["text"], seg_duration_s)
                                if refined and len(refined) > 1:
                                    offset_ms = int(results[0]["start"])
                                    for it in refined:
                                        it["start"] += offset_ms
                                        it["end"] += offset_ms
                                    refined[-1]["end"] = int(results[0]["end"])
                                    return refined
                            return results

                    if full_text:
                        logger.warning("未获得时间戳信息，回退为单一句子")
                        sentences = [{
                            "begin_time": 0,
                            "end_time": 0,
                            "text": full_text
                        }]
            
            for s in sentences:
                # 记录句子级的时间戳
                item = {
                    'start': s.get('begin_time', 0),
                    'end': s.get('end_time', 0),
                    'text': s.get('text', '').strip(),
                    'words': [] 
                }
                
                # 尝试获取词级时间戳
                words = s.get('words', [])
                if words:
                    for w in words:
                        item['words'].append({
                            'text': w.get('text'),
                            'start': w.get('begin_time'),
                            'end': w.get('end_time')
                        })
                
                if item['text']:
                    s_s = item['start'] / 1000.0
                    s_e = item['end'] / 1000.0
                    logger.info(f"  [{s_s:.2f}s - {s_e:.2f}s]: {item['text']}")
                    results.append(item)
            
            return results

        except Exception as e:
            logger.error(f"analyze_audio 捕获到异常: {e}")
            traceback.print_exc()
            return None
        finally:
            # 3. 资源清理逻辑
            if temp_audio_dir.exists():
                try:
                    shutil.rmtree(temp_audio_dir)
                    logger.info(f"已清理临时音频目录: {temp_audio_dir}")
                except Exception as e:
                    logger.warning(f"无法删除临时目录 {temp_audio_dir}: {e}")
            sys.stdout.flush()

    def _submit_asr_task(self, audio_path: str) -> Optional[Dict]:
        """提交 ASR 任务（使用 OpenAI 兼容 Chat 接口，支持时间戳和 Base64）。"""
        full_text = self._submit_asr_text(audio_path)
        if full_text is None:
            return None
        return {
            "output": {
                "sentences": [],
                "text": full_text
            }
        }

    def _get_speech_anchor_groups(self, results: List[Dict]) -> List[List[float]]:
        start_offset_s = float(getattr(config, "ANCHOR_START_OFFSET_S", 0.3))
        end_offset_s = float(getattr(config, "ANCHOR_END_OFFSET_S", 0.2))
        long_sentence_threshold_s = 6.0

        groups: List[List[float]] = []
        for res in results:
            anchors: List[float] = []

            start_ms = res.get("start")
            end_ms = res.get("end")
            start_s = (start_ms / 1000.0) if isinstance(start_ms, (int, float)) else None
            end_s = (end_ms / 1000.0) if isinstance(end_ms, (int, float)) else None
            words = res.get("words") or []

            if start_s is not None and end_s is not None and end_s > start_s:
                s_anchor = round(start_s + start_offset_s, 2)
                e_anchor = round(end_s - end_offset_s, 2)
                if e_anchor > s_anchor:
                    anchors.extend([s_anchor, e_anchor])
                    if (end_s - start_s) >= long_sentence_threshold_s:
                        mid_anchor = round((start_s + end_s) / 2.0, 2)
                        if s_anchor < mid_anchor < e_anchor:
                            anchors.append(mid_anchor)
                else:
                    anchors.append(round((start_s + end_s) / 2.0, 2))
            elif words:
                first_word = words[0]
                last_word = words[-1]
                fw_start_ms = first_word.get("start")
                lw_end_ms = last_word.get("end")
                if isinstance(fw_start_ms, (int, float)) and isinstance(lw_end_ms, (int, float)) and lw_end_ms > fw_start_ms:
                    s_anchor = round(fw_start_ms / 1000.0 + start_offset_s, 2)
                    e_anchor = round(lw_end_ms / 1000.0 - end_offset_s, 2)
                    if e_anchor > s_anchor:
                        anchors.extend([s_anchor, e_anchor])
                        if ((lw_end_ms - fw_start_ms) / 1000.0) >= long_sentence_threshold_s:
                            mid_ms = words[len(words) // 2].get("start") if len(words) > 2 else None
                            if isinstance(mid_ms, (int, float)):
                                anchors.append(round(mid_ms / 1000.0, 2))
                    else:
                        anchors.append(round((fw_start_ms + lw_end_ms) / 2000.0, 2))

            if anchors:
                groups.append(sorted(list(set(anchors))))

        return groups

    def _get_visual_anchors(self, video_path: str) -> List[float]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            logger.error(f"无法打开视频: {video_path}")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0

        anchors: List[float] = []
        sample_rate = 2
        last_frame_gray = None
        for t in np.arange(0, duration, 1.0 / sample_rate):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                break

            curr_gray = cv2.cvtColor(cv2.resize(frame, (64, 64)), cv2.COLOR_BGR2GRAY)
            if last_frame_gray is not None:
                diff = cv2.absdiff(curr_gray, last_frame_gray)
                score = np.mean(diff)
                if score > 30:
                    anchors.append(max(0, round(t - 0.1, 2)))
                    anchors.append(min(duration - 0.01, round(t + 0.1, 2)))
            last_frame_gray = curr_gray

        cap.release()
        return sorted(list(set([a for a in anchors if 0 <= a < duration])))

    def _get_visual_event_anchors(self, video_path: str, max_events: int = 12) -> List[Tuple[float, float]]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0

        sample_rate = 3
        last_frame_gray = None
        events: List[Tuple[float, float]] = []

        for t in np.arange(0, duration, 1.0 / sample_rate):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                break

            curr_gray = cv2.cvtColor(cv2.resize(frame, (96, 96)), cv2.COLOR_BGR2GRAY)
            if last_frame_gray is not None:
                diff = cv2.absdiff(curr_gray, last_frame_gray)
                mean_diff = float(np.mean(diff))
                p95 = float(np.percentile(diff, 95))
                ratio = float(np.mean(diff > 18))

                event_score = max(mean_diff / 28.0, p95 / 55.0, ratio / 0.08)
                if event_score >= 1.0:
                    events.append((round(float(t), 2), float(event_score)))
            last_frame_gray = curr_gray

        cap.release()

        if not events:
            return []

        events.sort(key=lambda x: x[1], reverse=True)
        selected: List[Tuple[float, float]] = []
        min_gap_s = 0.6
        for ts, score in events:
            if all(abs(ts - s_ts) >= min_gap_s for s_ts, _ in selected):
                selected.append((ts, score))
            if len(selected) >= max_events:
                break

        selected.sort(key=lambda x: x[0])
        return selected

    def _get_periodic_anchors(self, video_path: str, step_s: float = 2.5, max_points: int = 12) -> List[float]:
        duration = self._get_video_duration_s(video_path)
        if duration <= 0:
            return []
        if duration <= step_s:
            return [round(duration / 2.0, 2)]

        anchors = []
        t = 0.0
        while t < duration and len(anchors) < max_points:
            anchors.append(round(t, 2))
            t += step_s
        return anchors

    def _edge_text_score(self, img: np.ndarray) -> Tuple[float, float]:
        resized = cv2.resize(img, (256, 256))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 160)
        edge_ratio = float(np.mean(edges > 0))

        s = 64
        corners = [
            edges[0:s, 0:s],
            edges[0:s, -s:],
            edges[-s:, 0:s],
            edges[-s:, -s:]
        ]
        corner_ratio = float(max(np.mean(c > 0) for c in corners))
        return edge_ratio, corner_ratio

    def _is_similar_hash(self, h1: Dict, h2: Dict, threshold: int = 5) -> bool:
        dist = self._get_multi_distance(h1, h2)
        return not (
            dist["ahash"] > threshold
            or dist["dhash"] > threshold
            or dist["phash"] > threshold
            or dist["avg"] > 4
            or dist["pixel_diff"] > 15
        )

    def _sharpness_score(self, img: np.ndarray) -> float:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _dedup_within_group(self, frame_info: List[Tuple[float, str]], threshold: int = 5) -> Tuple[List[Tuple[float, str]], List[str]]:
        if not frame_info:
            return [], []

        loaded = []
        for ts, path in sorted(frame_info, key=lambda x: x[0]):
            img = self._cv2_imread_unicode(path)
            if img is None:
                continue
            loaded.append((ts, path, img, self._get_hashes(img), self._sharpness_score(img)))

        if len(loaded) <= 1:
            kept = [(ts, path) for ts, path, *_ in loaded]
            return kept, []

        kept = [loaded[0]]
        report_lines = []

        for curr in loaded[1:]:
            prev = kept[-1]
            ts1, p1, _img1, h1, s1 = prev
            ts2, p2, _img2, h2, s2 = curr
            dist = self._get_multi_distance(h1, h2)
            is_different = (
                dist["ahash"] > threshold
                or dist["dhash"] > threshold
                or dist["phash"] > threshold
                or dist["avg"] > 4
                or dist["pixel_diff"] > 15
            )

            if is_different:
                kept.append(curr)
                report_lines.append(f"[SentenceDedup] {ts1:.2f}s vs {ts2:.2f}s Keep:both")
                continue

            if s2 > s1:
                try:
                    Path(p1).unlink()
                except Exception:
                    pass
                kept[-1] = curr
                report_lines.append(f"[SentenceDedup] {ts1:.2f}s vs {ts2:.2f}s Keep:{ts2:.2f}s")
            else:
                try:
                    Path(p2).unlink()
                except Exception:
                    pass
                report_lines.append(f"[SentenceDedup] {ts1:.2f}s vs {ts2:.2f}s Keep:{ts1:.2f}s")

        return [(ts, path) for ts, path, *_ in kept], report_lines

    def _score_candidate_frame(self, img: np.ndarray, event_score: float) -> Dict[str, float]:
        sharp = self._sharpness_score(img)
        edge_ratio, corner_ratio = self._edge_text_score(img)
        score = math.log1p(max(sharp, 0.0)) + (6.0 * corner_ratio) + (2.0 * edge_ratio) + (1.5 * max(event_score, 0.0))
        return {
            "sharpness": float(sharp),
            "edge_ratio": float(edge_ratio),
            "corner_ratio": float(corner_ratio),
            "event_score": float(event_score),
            "score": float(score),
        }

    def _select_nine_by_slots(
        self,
        video_path: str,
        temp_dir: str,
        candidates: List[Dict[str, Any]],
        start_index: int,
    ) -> Tuple[List[Tuple[float, str]], List[str], int]:
        duration = self._get_video_duration_s(video_path)
        report_lines: List[str] = []
        if duration <= 0:
            sorted_cands = sorted(candidates, key=lambda x: x["ts"])
            picked = sorted_cands[:9]
            if picked:
                while len(picked) < 9:
                    picked.append(picked[-1])
            final_frames = [(c["ts"], c["path"]) for c in picked]
            report_lines.append("[SlotSelect] duration<=0，使用排序后兜底填充")
            return final_frames, report_lines, start_index

        slots = []
        for i in range(9):
            s = (duration * i) / 9.0
            e = (duration * (i + 1)) / 9.0
            slots.append({"idx": i, "start": s, "end": e, "center": (s + e) / 2.0})

        used_paths = set()
        selected: List[Optional[Dict[str, Any]]] = [None] * 9

        def is_usable(cand: Dict[str, Any], strict_similarity: bool) -> bool:
            if cand["path"] in used_paths:
                return False
            if strict_similarity:
                for s in selected:
                    if s is None:
                        continue
                    if self._is_similar_hash(cand["hashes"], s["hashes"]):
                        return False
            return True

        for slot in slots:
            in_slot = [c for c in candidates if slot["start"] <= c["ts"] < slot["end"]]
            in_slot.sort(key=lambda x: x["score"], reverse=True)
            for cand in in_slot:
                if is_usable(cand, strict_similarity=True):
                    selected[slot["idx"]] = cand
                    used_paths.add(cand["path"])
                    cand["selected_reason"] = "in_slot"
                    break

        for slot in slots:
            if selected[slot["idx"]] is not None:
                continue
            center = slot["center"]
            remaining = [c for c in candidates if c["path"] not in used_paths]
            remaining.sort(key=lambda x: (abs(x["ts"] - center), -x["score"]))
            for cand in remaining:
                if is_usable(cand, strict_similarity=True):
                    selected[slot["idx"]] = cand
                    used_paths.add(cand["path"])
                    cand["selected_reason"] = "nearest"
                    break
            if selected[slot["idx"]] is not None:
                continue
            for cand in remaining:
                if is_usable(cand, strict_similarity=False):
                    selected[slot["idx"]] = cand
                    used_paths.add(cand["path"])
                    cand["selected_reason"] = "nearest_relaxed"
                    break

        for slot in slots:
            if selected[slot["idx"]] is not None:
                continue
            center = round(float(slot["center"]), 2)
            extracted = self.extract_frames(video_path, [center], temp_dir, start_index=start_index)
            start_index += 1
            if not extracted:
                continue
            ts, path = extracted[0]
            img = self._cv2_imread_unicode(path)
            if img is None:
                try:
                    Path(path).unlink()
                except Exception:
                    pass
                continue
            cand = {
                "ts": float(ts),
                "path": path,
                "sources": {"fallback_extract"},
                "event_score": 0.0,
                "hashes": self._get_hashes(img),
            }
            cand.update(self._score_candidate_frame(img, 0.0))
            selected[slot["idx"]] = cand
            used_paths.add(path)
            cand["selected_reason"] = "slot_extract"
            candidates.append(cand)

        for slot in slots:
            if selected[slot["idx"]] is not None:
                continue
            left = slot["idx"] - 1
            right = slot["idx"] + 1
            source = None
            if 0 <= left < 9 and selected[left] is not None:
                source = selected[left]
            if source is None and 0 <= right < 9 and selected[right] is not None:
                source = selected[right]
            if source is not None:
                selected[slot["idx"]] = source
                if "sources" in source:
                    source["sources"] = set(source["sources"])
                else:
                    source["sources"] = set()
                source["sources"].add(f"fallback_copy_to_slot_{slot['idx']}")

        key_candidates = [c for c in candidates if c["path"] not in used_paths]
        key_candidates.sort(key=lambda x: (x.get("corner_ratio", 0.0), x["score"]), reverse=True)
        for cand in key_candidates[:12]:
            slot_idx = min(8, max(0, int((cand["ts"] / duration) * 9)))
            current = selected[slot_idx]
            if current is None:
                continue
            if cand["score"] <= current.get("score", 0.0) + 0.25:
                continue
            if self._is_similar_hash(cand["hashes"], current["hashes"]):
                continue
            selected[slot_idx] = cand
            used_paths.add(cand["path"])
            cand["selected_reason"] = "keynode_replace"

        final_frames: List[Tuple[float, str]] = []
        for slot in slots:
            cand = selected[slot["idx"]]
            if cand is None:
                continue
            final_frames.append((float(cand["ts"]), str(cand["path"])))
            sources = sorted(list(cand.get("sources") or []))
            report_lines.append(
                f"[Slot {slot['idx']}] {slot['start']:.2f}-{slot['end']:.2f}s pick={cand['ts']:.2f}s "
                f"reason={cand.get('selected_reason','')} sources={'+'.join(sources)} "
                f"score={cand.get('score',0.0):.3f} sharp={cand.get('sharpness',0.0):.1f} "
                f"corner={cand.get('corner_ratio',0.0):.3f} edge={cand.get('edge_ratio',0.0):.3f} event={cand.get('event_score',0.0):.2f}"
            )

        if final_frames:
            while len(final_frames) < 9:
                final_frames.append(final_frames[-1])
            if len(final_frames) > 9:
                final_frames = final_frames[:9]

        return final_frames, report_lines, start_index

    def _get_anchors(self, results: List[Dict], video_path: str) -> List[float]:
        """基于语音和视觉变化生成锚点。"""
        anchors = []
        if not results:
            return []

        start_offset_s = float(getattr(config, "ANCHOR_START_OFFSET_S", 0.3))
        end_offset_s = float(getattr(config, "ANCHOR_END_OFFSET_S", 0.2))
        long_sentence_midpoint = bool(getattr(config, "ANCHOR_LONG_SENTENCE_MIDPOINT", False))

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
            
        if not cap.isOpened():
            logger.error(f"无法打开视频: {video_path}")
            return []
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        
        # 1. 基于语音的锚点
        logger.info("正在生成基于语音的锚点...")
        for res in results:
            start_ms = res.get("start")
            end_ms = res.get("end")
            start_s = (start_ms / 1000.0) if isinstance(start_ms, (int, float)) else None
            end_s = (end_ms / 1000.0) if isinstance(end_ms, (int, float)) else None

            if start_s is not None and end_s is not None and end_s > start_s:
                s_anchor = round(start_s + start_offset_s, 2)
                e_anchor = round(end_s - end_offset_s, 2)
                if e_anchor > s_anchor:
                    anchors.append(s_anchor)
                    anchors.append(e_anchor)
                    if long_sentence_midpoint:
                        words = res.get("words") or []
                        if len(words) > 10:
                            mid_anchor = round((start_s + end_s) / 2.0, 2)
                            if s_anchor < mid_anchor < e_anchor:
                                anchors.append(mid_anchor)
                else:
                    anchors.append(round((start_s + end_s) / 2.0, 2))
                continue

            words = res.get("words") or []
            if words:
                first_word = words[0]
                last_word = words[-1]
                fw_start_ms = first_word.get("start")
                lw_end_ms = last_word.get("end")
                if isinstance(fw_start_ms, (int, float)) and isinstance(lw_end_ms, (int, float)) and lw_end_ms > fw_start_ms:
                    s_anchor = round(fw_start_ms / 1000.0 + start_offset_s, 2)
                    e_anchor = round(lw_end_ms / 1000.0 - end_offset_s, 2)
                    if e_anchor > s_anchor:
                        anchors.append(s_anchor)
                        anchors.append(e_anchor)
                        if long_sentence_midpoint and len(words) > 10:
                            mid_word = words[len(words) // 2]
                            mid_ms = mid_word.get("start")
                            if isinstance(mid_ms, (int, float)):
                                anchors.append(round(mid_ms / 1000.0, 2))
                    else:
                        anchors.append(round((fw_start_ms + lw_end_ms) / 2000.0, 2))

        # 2. 视觉变化检测
        logger.info("正在检测视觉变化...")
        sample_rate = 2 
        last_frame_gray = None
        for t in np.arange(0, duration, 1.0 / sample_rate):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret: break
            
            curr_gray = cv2.cvtColor(cv2.resize(frame, (64, 64)), cv2.COLOR_BGR2GRAY)
            if last_frame_gray is not None:
                diff = cv2.absdiff(curr_gray, last_frame_gray)
                score = np.mean(diff)
                if score > 30:
                    anchors.append(max(0, round(t - 0.1, 2)))
                    anchors.append(min(duration - 0.01, round(t + 0.1, 2)))
            last_frame_gray = curr_gray
        
        cap.release()

        final_anchors = sorted(list(set([a for a in anchors if 0 <= a < duration])))
        logger.info(f"总锚点数: {len(final_anchors)}")
        return final_anchors

    def extract_frames(self, video_path: str, anchors: List[float], temp_dir: str, start_index: int = 0) -> List[Tuple[float, str]]:
        """在锚点处提取帧。"""
        frame_paths = []
        temp_path = Path(temp_dir)
        temp_path.mkdir(exist_ok=True)
        
        for i, ts in enumerate(anchors):
            out_path = temp_path / f"frame_{start_index + i:04d}.jpg"
            cmd = [
                str(self.ffmpeg_exe), "-y", "-i", video_path, "-ss", str(ts),
                "-vframes", "1", "-q:v", "2", str(out_path), "-loglevel", "error"
            ]
            try:
                subprocess.run(cmd, check=True)
                if out_path.exists():
                    frame_paths.append((ts, str(out_path)))
            except Exception as e:
                print(f"[FFmpeg Error] 失败于 {ts}s: {e}")
        return frame_paths

    def _get_hashes(self, img: np.ndarray) -> Dict:
        """计算 aHash, dHash, pHash。"""
        resized = cv2.resize(img, (256, 256))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)

        # aHash
        ahash_img = cv2.resize(blurred, (8, 8), interpolation=cv2.INTER_AREA)
        avg = ahash_img.mean()
        ahash = "".join(['1' if p > avg else '0' for p in ahash_img.flatten()])

        # dHash
        dhash_img = cv2.resize(blurred, (9, 8), interpolation=cv2.INTER_AREA)
        dhash = ""
        for i in range(8):
            for j in range(8):
                dhash += "1" if dhash_img[i, j] > dhash_img[i, j+1] else "0"

        # pHash
        phash_img = cv2.resize(blurred, (32, 32), interpolation=cv2.INTER_AREA)
        dct = cv2.dct(np.float32(phash_img))
        dct_low = dct[:8, :8]
        p_avg = dct_low.mean()
        phash = "".join(['1' if p > p_avg else '0' for p in dct_low.flatten()])

        return {'ahash': ahash, 'dhash': dhash, 'phash': phash, 'raw_gray': blurred}

    def _get_multi_distance(self, h1: Dict, h2: Dict) -> Dict:
        """计算哈希之间的距离。"""
        d_a = sum(c1 != c2 for c1, c2 in zip(h1['ahash'], h2['ahash']))
        d_d = sum(c1 != c2 for c1, c2 in zip(h1['dhash'], h2['dhash']))
        d_p = sum(c1 != c2 for c1, c2 in zip(h1['phash'], h2['phash']))
        
        pixel_diff = np.mean(cv2.absdiff(h1['raw_gray'], h2['raw_gray']))
        
        return {
            'ahash': d_a, 'dhash': d_d, 'phash': d_p, 
            'avg': (d_a + d_d + d_p) / 3.0,
            'pixel_diff': pixel_diff
        }

    def _cv2_imread_unicode(self, path: str) -> Optional[np.ndarray]:
        """支持 unicode 路径读取图像。"""
        try:
            return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception as e:
            print(f"[Error] 读取图像失败 {path}: {e}")
            return None

    def remove_duplicate_frames(self, frame_info: List[Tuple[float, str]], threshold: int = 5, extra_report_lines: Optional[List[str]] = None) -> List[Tuple[float, str]]:
        """移除重复帧。"""
        if not frame_info: return []
        
        print(f"[Dedup] 正在去重 {len(frame_info)} 帧 (阈值: {threshold})...")
        
        frame_hashes = []
        for ts, path in frame_info:
            img = self._cv2_imread_unicode(path)
            if img is not None:
                frame_hashes.append({'ts': ts, 'path': path, 'hashes': self._get_hashes(img)})
            else:
                print(f"[Warning] 无法读取图像用于去重: {path}")

        if not frame_hashes: return []

        kept = [frame_hashes[0]]
        report_data = []
        filtered_pairs_count = 0

        for i in range(1, len(frame_hashes)):
            curr = frame_hashes[i]
            prev = kept[-1]
            
            dist = self._get_multi_distance(curr['hashes'], prev['hashes'])
            
            is_different = (
                dist['ahash'] > threshold or 
                dist['dhash'] > threshold or 
                dist['phash'] > threshold or 
                dist['avg'] > 4 or
                dist['pixel_diff'] > 15
            )
            
            if is_different:
                kept.append(curr)
            else:
                filtered_pairs_count += 1
            
            report_data.append({
                'ts_pair': (prev['ts'], curr['ts']),
                'distances': dist,
                'kept': is_different
            })

        final_list = kept
        if len(final_list) > 9:
            indices = np.linspace(0, len(final_list) - 1, 9).astype(int)
            final_list = [final_list[idx] for idx in indices]
            print(f"[Dedup] 帧数过多 ({len(kept)} > 9)，已重采样至 9。")
        
        elif len(final_list) < 3 and len(frame_hashes) >= 3:
            existing_paths = {f['path'] for f in final_list}
            candidates = [f for f in frame_hashes if f['path'] not in existing_paths]
            while len(final_list) < 3 and candidates:
                final_list.append(candidates.pop(len(candidates)//2))
            final_list.sort(key=lambda x: x['ts'])
            print(f"[Dedup] 帧数过少 ({len(kept)} < 3)，已从原始帧补充。")

        print(f"[Dedup] {len(frame_info)} -> {len(final_list)} (过滤了 {filtered_pairs_count})")
        
        # 清理删除的文件
        final_paths = {f['path'] for f in final_list}
        for ts, p in frame_info:
            if p not in final_paths:
                try: Path(p).unlink()
                except: pass

        # 生成报告
        report_path = Path(frame_info[0][1]).parent.parent / "dedup_report.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            if extra_report_lines:
                for line in extra_report_lines:
                    f.write(line.rstrip("\n") + "\n")
                f.write("\n")
            f.write("=== 去重报告 ===\n")
            f.write(f"原始: {len(frame_info)}\n")
            f.write(f"保留: {len(final_list)}\n\n")
            for item in report_data:
                f.write(f"[{item['ts_pair'][0]:.2f}s vs {item['ts_pair'][1]:.2f}s] ")
                f.write(f"pHash:{item['distances']['phash']} dHash:{item['distances']['dhash']} ")
                f.write(f"Diff:{item['distances']['pixel_diff']:.2f} Keep:{item['kept']}\n")
        
        return [(f['ts'], f['path']) for f in final_list]

    def create_contact_sheet(self, frame_info: List[Tuple[float, str]], output_base_path: str) -> List[str]:
        """创建拼图 (九宫格)。"""
        if not frame_info: return []
            
        total_frames = len(frame_info)
        chunk_size = 9
        output_files = []

        for chunk_idx, i in enumerate(range(0, total_frames, chunk_size)):
            chunk = frame_info[i : i + chunk_size]
            if len(chunk) < chunk_size:
                last = chunk[-1]
                while len(chunk) < chunk_size:
                    chunk.append(last)
            cols, rows = 3, 3

            max_side = 400
            processed_imgs = []
            try:
                font = ImageFont.truetype("arial.ttf", 22)
            except:
                font = ImageFont.load_default()

            for ts, p in chunk:
                with Image.open(p) as img:
                    img = img.convert("RGB")
                    img.thumbnail((max_side, max_side))
                    
                    draw = ImageDraw.Draw(img, "RGBA")
                    ts_text = f"{ts:.2f}s"
                    
                    bbox = draw.textbbox((5, 5), ts_text, font=font)
                    draw.rectangle([bbox[0]-2, bbox[1]-2, bbox[2]+2, bbox[3]+2], fill=(0,0,0,160))
                    draw.text((5, 5), ts_text, fill="white", font=font)
                    processed_imgs.append(img.copy())

            cell_w, cell_h = processed_imgs[0].size
            canvas = Image.new('RGB', (cell_w * cols, cell_h * rows), (30, 30, 30))
            for idx, img in enumerate(processed_imgs):
                canvas.paste(img, ((idx % cols) * cell_w, (idx // cols) * cell_h))

            if total_frames <= chunk_size:
                save_path = output_base_path
            else:
                sheet_name = f"contact_sheet_{chunk_idx + 1}.jpg"
                save_path = output_base_path.replace("final_sheet.jpg", sheet_name)
            
            canvas.save(save_path, "JPEG", quality=95)
            logger.info(f"拼图已保存: {save_path}")
            output_files.append(save_path)

        return output_files

def process_video_folder(video_folder: Path, output_root: Path, progress_callback=None):
    """处理文件夹中的所有视频。"""
    analyzer = VideoAnalyzer()

    valid_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.ts')
    
    if not video_folder.exists():
        logger.error(f"视频文件夹不存在: {video_folder}")
        if progress_callback:
            progress_callback(f"❌ 视频文件夹不存在: {video_folder}")
        return

    video_files = [f for f in video_folder.iterdir() if f.suffix.lower() in valid_extensions]
    
    if not video_files:
        logger.warning(f"未找到有效视频: {video_folder}")
        if progress_callback:
            progress_callback(f"⚠️ 文件夹中没有找到有效视频: {video_folder}")
        return

    logger.info(f"发现 {len(video_files)} 个视频")
    
    # 阶段 1: 音频提取 & ASR
    if progress_callback:
        progress_callback(f"🎵 正在提取音频并进行语音识别，共计 {len(video_files)} 条...")

    audio_success_count = 0
    for video_file in video_files:
        video_name = video_file.name
        video_basename = video_file.stem
        
        video_out_dir = output_root / video_basename
        video_out_dir.mkdir(parents=True, exist_ok=True)
        
        transcript_path = video_out_dir / "transcript_detailed.txt"
        
        # 检查字幕是否存在
        if transcript_path.exists():
            audio_success_count += 1
            continue

        logger.info(f"\n>>> 正在处理音频: {video_name}")
        results = analyzer.analyze_audio(str(video_file), str(video_out_dir))
        
        if results:
            with open(transcript_path, "w", encoding="utf-8") as f:
                for item in results:
                    f.write(f"[{item['start']/1000:.2f}s - {item['end']/1000:.2f}s] {item['text']}\n")
            audio_success_count += 1
        else:
            # 关键：识别失败，记录详细原因并根据需求中断任务
            error_detail = analyzer.last_error or "未知原因（可能未检测到语音）"
            logger.error(f"视频: {video_name}, 原因: {error_detail}")
            
            if progress_callback:
                progress_callback(f"❌ 语音上传/识别失败: {video_name}\n原因: {error_detail}")
            
            # 抛出异常中断整个任务管线
            raise Exception(f"语音识别链路中断：{error_detail}")

        # 阶段 2: 截图
        if progress_callback:
            progress_callback(f"🖼️ 正在进行视频截图...")
            
        video_out_dir = output_root / video_basename
        image_out_dir = video_out_dir / "cache_images"
        sheet_path = video_out_dir / "final_sheet.jpg"
        
        if not sheet_path.exists() and results:
            logger.info(f"\n>>> 正在处理图像: {video_name}")
            speech_groups = analyzer._get_speech_anchor_groups(results)
            duration_s = analyzer._get_video_duration_s(str(video_file))
            periodic_anchors = analyzer._get_periodic_anchors(str(video_file))
            visual_events = analyzer._get_visual_event_anchors(str(video_file), max_events=10)

            frame_info = []
            extra_report_lines = []
            start_index = 0
            meta_by_ts = {}
            all_paths = set()

            for group in speech_groups:
                group_frames = analyzer.extract_frames(str(video_file), group, str(image_out_dir), start_index=start_index)
                start_index += len(group)
                kept_group, group_lines = analyzer._dedup_within_group(group_frames)
                frame_info.extend(kept_group)
                extra_report_lines.extend(group_lines)
                for ts, p in kept_group:
                    k = round(float(ts), 2)
                    meta = meta_by_ts.setdefault(k, {"sources": set(), "event_score": 0.0})
                    meta["sources"].add("speech")
                    all_paths.add(p)

            used_ts = {round(float(ts), 2) for ts, _ in frame_info}
            anchor_meta = {}
            for ts in periodic_anchors:
                k = round(float(ts), 2)
                if k in used_ts:
                    continue
                anchor_meta.setdefault(k, {"sources": set(), "event_score": 0.0})
                anchor_meta[k]["sources"].add("periodic")

            for t, score in visual_events:
                for dt in (-0.2, 0.0, 0.2):
                    ts = round(float(t + dt), 2)
                    if duration_s > 0:
                        ts = max(0.0, min(float(duration_s - 0.01), ts))
                    k = round(float(ts), 2)
                    if k in used_ts:
                        continue
                    anchor_meta.setdefault(k, {"sources": set(), "event_score": 0.0})
                    anchor_meta[k]["sources"].add("event")
                    anchor_meta[k]["event_score"] = max(float(anchor_meta[k]["event_score"]), float(score))

            other_anchor_times = sorted(anchor_meta.keys())
            if other_anchor_times:
                other_frames = analyzer.extract_frames(str(video_file), other_anchor_times, str(image_out_dir), start_index=start_index)
                start_index += len(other_anchor_times)
                for ts, p in other_frames:
                    k = round(float(ts), 2)
                    meta = anchor_meta.get(k)
                    if meta:
                        meta_by_ts.setdefault(k, {"sources": set(), "event_score": 0.0})
                        meta_by_ts[k]["sources"].update(meta["sources"])
                        meta_by_ts[k]["event_score"] = max(float(meta_by_ts[k]["event_score"]), float(meta["event_score"]))
                    frame_info.append((float(ts), p))
                    all_paths.add(p)

            frame_info.sort(key=lambda x: x[0])
            
            if frame_info:
                candidates = []
                for ts, p in frame_info:
                    img = analyzer._cv2_imread_unicode(p)
                    if img is None:
                        continue
                    k = round(float(ts), 2)
                    meta = meta_by_ts.get(k, {"sources": set(), "event_score": 0.0})
                    event_score = float(meta.get("event_score", 0.0) or 0.0)
                    cand = {
                        "ts": float(ts),
                        "path": p,
                        "sources": set(meta.get("sources") or []),
                        "event_score": event_score,
                        "hashes": analyzer._get_hashes(img),
                    }
                    cand.update(analyzer._score_candidate_frame(img, event_score))
                    candidates.append(cand)

                final_frames, slot_report_lines, start_index = analyzer._select_nine_by_slots(
                    str(video_file),
                    str(image_out_dir),
                    candidates,
                    start_index,
                )

                keep_paths = {p for _, p in final_frames}
                for p in all_paths:
                    if p in keep_paths:
                        continue
                    try:
                        Path(p).unlink()
                    except Exception:
                        pass

                report_path = video_out_dir / "dedup_report.txt"
                with open(report_path, "w", encoding="utf-8") as f:
                    for line in extra_report_lines:
                        f.write(line.rstrip("\n") + "\n")
                    if extra_report_lines:
                        f.write("\n")
                    f.write("=== 最终九宫格选帧（按9个时间槽） ===\n")
                    for line in slot_report_lines:
                        f.write(line.rstrip("\n") + "\n")
                    f.write("\n")
                    f.write(f"候选总数: {len(candidates)}\n")
                    f.write(f"最终输出: {len(final_frames)}\n")

                analyzer.create_contact_sheet(final_frames, str(sheet_path))
                print(f"[Done] 完成图像处理: {video_name}")
            else:
                print(f"[Warning] 未提取到有效帧: {video_name}")

        # --- 自动删除视频以节省空间 ---
        try:
            print(f"[Cleanup] 正在删除临时视频: {video_name}")
            video_file.unlink()
        except Exception as e:
            print(f"[Cleanup Error] 删除失败 {video_name}: {e}")

    analyzer.release_model()
    if progress_callback:
        progress_callback("✅ 视频预处理（音频+截图）全部完成！")

