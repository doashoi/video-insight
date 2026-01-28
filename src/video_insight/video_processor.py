import os
import sys
import subprocess
import gc
import re
import math
import shutil
import traceback
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import cv2
import torch
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 如果需要，添加隐式依赖项到 sys.path，或者依赖已安装的包。
# funasr 和 modelscope 是已安装的包。

from .config import config

class VideoAnalyzer:
    def __init__(self):
        """使用配置中的模型和路径初始化 VideoAnalyzer。"""
        self.model_dir = config.MODEL_DIR
        self.vad_model_dir = config.VAD_MODEL_DIR
        self.ffmpeg_exe = config.FFMPEG_PATH
        
        # 注册 FFmpeg 路径
        ffmpeg_dir = os.path.dirname(self.ffmpeg_exe)
        if ffmpeg_dir and ffmpeg_dir not in os.environ["PATH"]:
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]
            print(f"[Init] FFmpeg 路径已注册: {ffmpeg_dir}")

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.asr_model = None
        self.vad_model = None

    def release_model(self):
        """释放 GPU 内存。"""
        if self.asr_model is not None:
            del self.asr_model
        if self.vad_model is not None:
            del self.vad_model
        self.asr_model = None
        self.vad_model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print("[System] GPU 内存已释放")

    def extract_audio_track(self, video_path: str, audio_path: str) -> bool:
        """从视频提取音频 (16k, mono, pcm_s16le)。"""
        cmd = [
            str(self.ffmpeg_exe), "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            audio_path, "-loglevel", "error"
        ]
        try:
            print(f"[Audio] 正在提取音频到: {audio_path}")
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"[Error] 音频提取失败: {e}")
            return False

    def _load_models(self):
        """延迟加载模型。"""
        if self.asr_model is None or self.vad_model is None:
            # 如果需要，将 model_dir 添加到 sys.path 以确保本地导入工作
            # 注意: 通常 funasr 会处理这个，但为了安全起见我们保留它，以防模型依赖本地代码
            if str(self.model_dir) not in sys.path:
                sys.path.insert(0, str(self.model_dir))
            
            try:
                from funasr import AutoModel
            except ImportError:
                print("[Error] 缺少 'funasr' 库。请安装它: pip install funasr")
                raise

            print(f"[Init] 正在 {self.device} 上加载模型...")
            
            try:
                print(f"[Init] 正在加载 VAD 模型: {self.vad_model_dir.name}")
                self.vad_model = AutoModel(
                    model=str(self.vad_model_dir),
                    trust_remote_code=True,
                    device=self.device,
                    disable_update=True
                )

                print(f"[Init] 正在加载 SenseVoice 模型: {self.model_dir.name}")
                self.asr_model = AutoModel(
                    model=str(self.model_dir),
                    trust_remote_code=False,
                    device=self.device,
                    disable_update=True
                )
            except Exception as e:
                print(f"[Error] 模型加载失败: {e}")
                raise

    def analyze_audio(self, video_path: str, output_dir: str) -> Optional[List[Dict]]:
        """在视频音频上运行 VAD + ASR。"""
        temp_audio_dir = Path(output_dir) / "temp_audio"
        temp_audio_dir.mkdir(exist_ok=True)
        audio_path = temp_audio_dir / "full_audio.wav"
        
        if not self.extract_audio_track(video_path, str(audio_path)):
            return None

        try:
            self._load_models()
        except Exception:
            return None

        print(f"[Analysis] 正在处理音频: {audio_path.name}")
        results = []
        try:
            # 1. VAD (语音活动检测)
            print("[VAD] 正在检测语音片段...")
            vad_res = self.vad_model.generate(
                input=str(audio_path),
                max_single_segment_time=1500,
                max_end_silence_time=150,
                min_start_silence_time=100,
                min_speech_duration_ms=100,
            )
            
            segments = []
            if vad_res and len(vad_res) > 0 and 'value' in vad_res[0]:
                segments = vad_res[0]['value']
                print(f"[VAD] 发现 {len(segments)} 个片段")
            else:
                print(f"[Warning] 未检测到语音 (原始结果: {vad_res})")
                return None

            # 2. ASR (自动语音识别)
            print("[ASR] 正在识别语音...")
            clean_pattern = re.compile(r'<\|.*?\|>')
            
            for i, (start_ms, end_ms) in enumerate(segments):
                chunk_path = temp_audio_dir / f"chunk_{i:03d}.wav"
                duration_s = (end_ms - start_ms) / 1000.0
                start_s = start_ms / 1000.0
                
                cmd = [
                    str(self.ffmpeg_exe), "-y", "-i", str(audio_path),
                    "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}",
                    "-c", "copy", str(chunk_path), "-loglevel", "error"
                ]
                subprocess.run(cmd, check=True)
                
                asr_res = self.asr_model.generate(
                    input=str(chunk_path),
                    cache={},
                    language="zh",
                    use_itn=True,
                    batch_size_s=60,
                    merge_vad=False, 
                    return_spk_res=False,
                )
                
                raw_sentences = []
                if asr_res and len(asr_res) > 0:
                    raw_text = asr_res[0].get('text', '')
                    clean_text = clean_pattern.sub('', raw_text).strip()
                    clean_text = re.sub(r'\s+', ' ', clean_text)
                    
                    if clean_text:
                        punc_list = "，。！？；"
                        split_pattern = f'([^{punc_list}]+[{punc_list}]?)'
                        sub_sentences = re.findall(split_pattern, clean_text)
                        
                        if len(sub_sentences) > 1:
                            total_chars = len(clean_text)
                            current_ms = start_ms
                            for sub_s in sub_sentences:
                                sub_len = len(sub_s)
                                ratio = sub_len / total_chars
                                sub_duration = (end_ms - start_ms) * ratio
                                raw_sentences.append({
                                    'start': current_ms,
                                    'end': current_ms + sub_duration,
                                    'text': sub_s.strip()
                                })
                                current_ms += sub_duration
                        else:
                            raw_sentences.append({
                                'start': start_ms,
                                'end': end_ms,
                                'text': clean_text
                            })

                for item in raw_sentences:
                    text = item['text']
                    if text:
                        s_s = item['start'] / 1000.0
                        s_e = item['end'] / 1000.0
                        print(f"  [{s_s:.2f}s - {s_e:.2f}s]: {text}")
                        results.append(item)
                
                try: chunk_path.unlink()
                except: pass
                
            return results

        except Exception as e:
            print(f"[Error] 推理失败: {e}")
            traceback.print_exc()
            return None

    def _get_anchors(self, results: List[Dict], video_path: str) -> List[float]:
        """基于语音和视觉变化生成锚点。"""
        anchors = []
        if not results:
            return []

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
            
        if not cap.isOpened():
            print(f"[Error] 无法打开视频: {video_path}")
            return []
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        
        # 1. 基于语音的锚点
        print("[Anchors] 正在生成基于语音的锚点...")
        for res in results:
            start_s = res['start'] / 1000.0
            end_s = res['end'] / 1000.0
            
            s_anchor = round(start_s + 0.3, 2)
            e_anchor = round(end_s - 0.2, 2)
            
            if e_anchor > s_anchor:
                anchors.append(s_anchor)
                anchors.append(e_anchor)
            else:
                anchors.append(round((start_s + end_s) / 2, 2))

        # 2. 视觉变化检测
        print("[Anchors] 正在检测视觉变化...")
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
        print(f"[Anchors] 总锚点数: {len(final_anchors)}")
        return final_anchors

    def extract_frames(self, video_path: str, anchors: List[float], temp_dir: str) -> List[Tuple[float, str]]:
        """在锚点处提取帧。"""
        frame_paths = []
        temp_path = Path(temp_dir)
        temp_path.mkdir(exist_ok=True)
        
        for i, ts in enumerate(anchors):
            out_path = temp_path / f"frame_{i:04d}.jpg"
            cmd = [
                str(self.ffmpeg_exe), "-y", "-ss", str(ts), "-i", video_path,
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

    def remove_duplicate_frames(self, frame_info: List[Tuple[float, str]], threshold: int = 5) -> List[Tuple[float, str]]:
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

        with Image.open(frame_info[0][1]) as first_img:
            w, h = first_img.size
            is_portrait = h > w

        for chunk_idx, i in enumerate(range(0, total_frames, chunk_size)):
            chunk = frame_info[i : i + chunk_size]
            num_in_chunk = len(chunk)
            
            if num_in_chunk <= 3:
                cols, rows = (1, num_in_chunk) if not is_portrait else (num_in_chunk, 1)
            elif num_in_chunk <= 4:
                cols, rows = 2, 2
            elif num_in_chunk <= 6:
                cols, rows = (2, 3) if not is_portrait else (3, 2)
            else:
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
            print(f"[Success] 拼图已保存: {save_path}")
            output_files.append(save_path)

        return output_files

def process_video_folder(video_folder: Path, output_root: Path, progress_callback=None):
    """处理文件夹中的所有视频。"""
    analyzer = VideoAnalyzer()

    valid_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.ts')
    
    if not video_folder.exists():
        print(f"[Error] 视频文件夹不存在: {video_folder}")
        if progress_callback:
            progress_callback(f"❌ 视频文件夹不存在: {video_folder}")
        return

    video_files = [f for f in video_folder.iterdir() if f.suffix.lower() in valid_extensions]
    
    if not video_files:
        print(f"[Warning] 未找到有效视频: {video_folder}")
        if progress_callback:
            progress_callback(f"⚠️ 文件夹中没有找到有效视频: {video_folder}")
        return

    print(f"[Batch] 发现 {len(video_files)} 个视频")
    
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

        print(f"\n>>> 正在处理音频: {video_name}")
        results = analyzer.analyze_audio(str(video_file), str(video_out_dir))
        
        if results:
            with open(transcript_path, "w", encoding="utf-8") as f:
                for item in results:
                    f.write(f"[{item['start']/1000:.2f}s - {item['end']/1000:.2f}s] {item['text']}\n")
            audio_success_count += 1
        else:
            print(f"[Skip] 未检测到语音或音频失败: {video_name}")
            if progress_callback:
                progress_callback(f"⚠️ 音频提取失败: {video_name}")

    # 阶段 2: 截图
    if progress_callback:
        progress_callback(f"🖼️ 音频提取完成，正在进行截图，共计 {len(video_files)} 条...")
        
    for video_file in video_files:
        video_name = video_file.name
        video_basename = video_file.stem
        video_out_dir = output_root / video_basename
        
        image_out_dir = video_out_dir / "cache_images"
        sheet_path = video_out_dir / "final_sheet.jpg"
        transcript_path = video_out_dir / "transcript_detailed.txt"
        
        if sheet_path.exists():
            continue
            
        if not transcript_path.exists():
            continue
            
        print(f"\n>>> 正在处理图像: {video_name}")
        
        # 从字幕重新加载结果 (简化解析，或如果需要重新运行分析？
        # 重新运行分析很昂贵。我们需要将字幕解析回 'results' 格式供 _get_anchors 使用)
        # 实际上 _get_anchors 需要毫秒级的 'start' 和 'end'。
        # 让我们解析字幕文件。
        results = []
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    # 格式: [0.00s - 1.20s] Text
                    parts = line.strip().split("] ")
                    if len(parts) >= 2:
                        time_part = parts[0][1:] # 0.00s - 1.20s
                        times = time_part.split(" - ")
                        start_ms = float(times[0].replace("s", "")) * 1000
                        end_ms = float(times[1].replace("s", "")) * 1000
                        results.append({'start': start_ms, 'end': end_ms})
        except Exception as e:
            print(f"[Error] 解析字幕失败 {video_name}: {e}")
            continue

        anchors = analyzer._get_anchors(results, str(video_file))
        frame_info = analyzer.extract_frames(str(video_file), anchors, str(image_out_dir))
        
        final_frames = analyzer.remove_duplicate_frames(frame_info)
        analyzer.create_contact_sheet(final_frames, str(sheet_path))
        
        print(f"[Done] 完成图像处理: {video_name}")

        # --- 自动删除视频以节省空间 ---
        try:
            print(f"[Cleanup] 正在删除临时视频: {video_name}")
            video_file.unlink()
        except Exception as e:
            print(f"[Cleanup Error] 删除失败 {video_name}: {e}")

    analyzer.release_model()
    if progress_callback:
        progress_callback("✅ 视频预处理（音频+截图）全部完成！")
