import streamlit as st
import subprocess
import random
import os
import tempfile
import uuid
import re

st.set_page_config(page_title="Avatar Video Toolkit", layout="centered")
st.title("🎬 Avatar Video Toolkit")

tab1, tab2, tab3 = st.tabs(["🪝 Hook Builder", "🌄 Background Generator", "🎞️ Variation Generator"])

# ============== SHARED HELPERS ==============

def save_upload(file, tmpdir, prefix):
    path = os.path.join(tmpdir, f"{prefix}_{uuid.uuid4().hex[:6]}.mp4")
    with open(path, "wb") as f:
        f.write(file.read())
    return path

def get_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration", "-of",
           "default=noprint_wrappers=1:nokey=1", path]
    return float(subprocess.check_output(cmd).strip())

def has_audio(path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a",
           "-show_entries", "stream=codec_type", "-of", "csv=p=0", path]
    return "audio" in subprocess.run(cmd, capture_output=True, text=True).stdout

def add_silent_audio(path, tmpdir):
    out = os.path.join(tmpdir, f"silent_{uuid.uuid4().hex[:6]}.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-i", path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "copy", "-c:a", "aac", "-shortest", out
    ], capture_output=True, check=True)
    return out

def trim_video(path, max_seconds, tmpdir):
    dur = get_duration(path)
    if dur <= max_seconds:
        return path
    out = os.path.join(tmpdir, f"trim_{uuid.uuid4().hex[:6]}.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-i", path, "-t", str(max_seconds),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", out
    ], capture_output=True, check=True)
    return out

def detect_silence_cuts(audio_path, min_silence_seconds=0.25, silence_db=-30):
    """Returns list of timestamps (seconds) where speech pauses occur — natural cut points."""
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", f"silencedetect=noise={silence_db}dB:d={min_silence_seconds}",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr
    
    # Parse silence_end events — these mark where speech resumes (good cut points)
    cuts = []
    for line in output.split("\n"):
        match = re.search(r"silence_end:\s*([\d.]+)", line)
        if match:
            cuts.append(float(match.group(1)))
    return cuts

def composite_main_smart_cuts(bg_path, avatar_path, w, h, output_path, 
                              min_pause=0.25, silence_db=-30, fallback_min_cut=2.0, fallback_max_cut=3.5):
    """Composite avatar over background with cuts on natural speech pauses."""
    duration = get_duration(avatar_path)
    
    # Detect speech pauses in the avatar's audio
    pause_points = detect_silence_cuts(avatar_path, min_pause, silence_db)
    
    # Build cut segments from pause points, with a fallback timer if pauses are too sparse
    cut_times = [0.0]
    last = 0.0
    for p in pause_points:
        if p - last >= 1.5:  # don't cut more frequently than every 1.5s
            cut_times.append(p)
            last = p
    
    # If pauses are too rare (e.g., long monologue with no breaks), insert fallback cuts
    final_cuts = [0.0]
    for i in range(1, len(cut_times)):
        gap = cut_times[i] - final_cuts[-1]
        if gap > fallback_max_cut + 1.0:
            # Fill the gap with fallback-timed cuts
            t = final_cuts[-1] + random.uniform(fallback_min_cut, fallback_max_cut)
            while t < cut_times[i]:
                final_cuts.append(t)
                t += random.uniform(fallback_min_cut, fallback_max_cut)
        final_cuts.append(cut_times[i])
    
    # Make sure we extend to end of video
    if final_cuts[-1] < duration - 1.0:
        t = final_cuts[-1] + random.uniform(fallback_min_cut, fallback_max_cut)
        while t < duration:
            final_cuts.append(t)
            t += random.uniform(fallback_min_cut, fallback_max_cut)
    final_cuts.append(duration)
    
    # Build segments (start, end) from consecutive cut points
    zones = [
        (0.03, 0.05, 0.40),   # top-left small
        (0.57, 0.05, 0.40),   # top-right small
        (0.03, 0.55, 0.45),   # bottom-left medium
        (0.55, 0.55, 0.45),   # bottom-right medium
        (0.30, 0.30, 0.40),   # center small
        (0.03, 0.30, 0.45),   # left middle
        (0.55, 0.30, 0.45),   # right middle
        (0.20, 0.50, 0.55),   # bottom-center large
    ]
    
    segments = []
    last_zone = None
    for i in range(len(final_cuts) - 1):
        start, end = final_cuts[i], final_cuts[i+1]
        if end - start < 0.5:
            continue  # skip super-short segments
        available = [z for z in zones if z != last_zone]
        zone = random.choice(available)
        last_zone = zone
        x = int(w * zone[0])
        y = int(h * zone[1])
        segments.append((start, end, x, y, zone[2]))
    
    if not segments:
        # fallback: one segment for the whole video
        zone = random.choice(zones)
        segments = [(0, duration, int(w * zone[0]), int(h * zone[1]), zone[2])]
    
    filters = [
        f"[0:v]scale={w}:{h},setsar=1[bg]",
        "[1:v]colorkey=0x00FF00:0.30:0.15[keyed]"
    ]
    chain = "[bg]"
    for i, (start, end, x, y, scale) in enumerate(segments):
        sw = int(w * scale)
        filters.append(f"[keyed]scale={sw}:-1[s{i}]")
        next_label = f"[v{i}]" if i < len(segments) - 1 else "[outv]"
        chain += f"[s{i}]overlay={x}:{y}:enable='between(t,{start:.2f},{end:.2f})'{next_label}"
        if i < len(segments) - 1:
            filters.append(chain)
            chain = f"[v{i}]"
    filters.append(chain)
    full_filter = ";".join(filters)
    
    cmd = [
        "ffmpeg", "-y", "-i", bg_path, "-i", avatar_path,
        "-filter_complex", full_filter,
        "-map", "[outv]", "-map", "1:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-shortest", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    
    return len(segments)

def concat_videos(paths, w, h, output_path, tmpdir, crossfade_seconds=0):
    normalized = []
    for p in paths:
        if not has_audio(p):
            p = add_silent_audio(p, tmpdir)
        normalized.append(p)
    
    inputs = []
    for p in normalized:
        inputs.extend(["-i", p])
    
    n = len(normalized)
    
    if crossfade_seconds <= 0 or n == 1:
        filter_parts = []
        concat_inputs = ""
        for i in range(n):
            filter_parts.append(f"[{i}:v]scale={w}:{h},setsar=1,fps=30[v{i}]")
            concat_inputs += f"[v{i}][{i}:a]"
        filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")
        full_filter = ";".join(filter_parts)
    else:
        filter_parts = []
        durations = [get_duration(p) for p in normalized]
        for i in range(n):
            filter_parts.append(f"[{i}:v]scale={w}:{h},setsar=1,fps=30[v{i}]")
        
        prev_v = "v0"
        prev_a = "0:a"
        offset = durations[0] - crossfade_seconds
        for i in range(1, n):
            out_v = f"vx{i}" if i < n - 1 else "outv"
            out_a = f"ax{i}" if i < n - 1 else "outa"
            filter_parts.append(
                f"[{prev_v}][v{i}]xfade=transition=fade:duration={crossfade_seconds}:offset={offset:.2f}[{out_v}]"
            )
            filter_parts.append(
                f"[{prev_a}][{i}:a]acrossfade=d={crossfade_seconds}[{out_a}]"
            )
            prev_v = out_v
            prev_a = out_a
            offset += durations[i] - crossfade_seconds
        full_filter = ";".join(filter_parts)
    
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", full_filter,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])

# ============== TAB 1: HOOK BUILDER (BULK) ==============

with tab1:
    st.header("Build hooks in bulk")
    st.write("Upload multiple Video A's and multiple Video B's. Get every combination, or N random pairings.")
    
    video_a_files = st.file_uploader(
        "Video A — hook openers (upload many variations)",
        type=["mp4", "mov"], accept_multiple_files=True, key="hb_a"
    )
    video_b_files = st.file_uploader(
        "Video B — end scenes (upload many variations)",
        type=["mp4", "mov"], accept_multiple_files=True, key="hb_b"
    )
    
    st.subheader("Settings")
    pairing_mode = st.radio(
        "How to pair them?",
        ["Every combination (A × B)", "Random pairings (pick N)"],
        key="hb_mode"
    )
    
    if pairing_mode == "Random pairings (pick N)":
        num_random = st.slider("How many random hook variations?", 1, 50, 10, key="hb_num")
    
    hb_output_size = st.selectbox(
        "Output size",
        ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"],
        key="hb_size"
    )
    hb_crossfade = st.checkbox("Crossfade between A and B (0.3s fade)", value=False, key="hb_xfade")
    
    if st.button("🪝 Build Hooks", type="primary", key="hb_btn"):
        if not video_a_files or not video_b_files:
            st.error("Please upload at least one Video A and one Video B.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = [int(x) for x in hb_output_size.split(" ")[0].split("x")]
            
            with st.spinner("Saving uploads..."):
                a_paths = [(f.name, save_upload(f, tmpdir, "vidA")) for f in video_a_files]
                b_paths = [(f.name, save_upload(f, tmpdir, "vidB")) for f in video_b_files]
            
            # Build pairing list
            if pairing_mode == "Every combination (A × B)":
                pairings = [(a, b) for a in a_paths for b in b_paths]
            else:
                pairings = [(random.choice(a_paths), random.choice(b_paths)) for _ in range(num_random)]
            
            st.info(f"Building {len(pairings)} hook variations...")
            
            progress = st.progress(0)
            status = st.empty()
            results = []
            
            for i, ((a_name, a_path), (b_name, b_path)) in enumerate(pairings):
                status.text(f"Building {i+1} of {len(pairings)}: {a_name} + {b_name}")
                try:
                    job_id = uuid.uuid4().hex[:6]
                    output_path = os.path.join(tmpdir, f"hook_{i+1}_{job_id}.mp4")
                    xfade = 0.3 if hb_crossfade else 0
                    concat_videos([a_path, b_path], w, h, output_path, tmpdir, crossfade_seconds=xfade)
                    results.append((i+1, output_path, a_name, b_name))
                except Exception as e:
                    st.error(f"Hook {i+1} failed: {str(e)[:500]}")
                
                progress.progress((i+1) / len(pairings))
            
            status.text(f"✅ Done — {len(results)} hooks built.")
            
            for idx, path, a_name, b_name in results:
                with st.expander(f"Hook {idx}: {a_name} + {b_name}", expanded=(idx == 1)):
                    with open(path, "rb") as f:
                        video_bytes = f.read()
                    st.video(video_bytes)
                    st.download_button(
                        f"⬇️ Download Hook {idx}",
                        video_bytes,
                        file_name=f"hook_{idx}.mp4",
                        mime="video/mp4",
                        key=f"hbdl_{idx}"
                    )

# ============== TAB 2: BACKGROUND GENERATOR ==============

with tab2:
    st.header("Generate a randomized background")
    st.write("Upload a library of clips. Each generation picks random clips and stitches them into a unique background.")
    
    bg_library = st.file_uploader(
        "Background clip library (upload many)",
        type=["mp4", "mov"], accept_multiple_files=True, key="bg_lib"
    )
    
    st.subheader("Settings")
    target_length = st.slider("Target background length (seconds)", 15, 180, 60, 5, key="bg_target")
    clip_min = st.slider("Shortest clip segment (seconds)", 1.0, 10.0, 3.0, 0.5, key="bg_clipmin")
    clip_max = st.slider("Longest clip segment (seconds)", 2.0, 15.0, 6.0, 0.5, key="bg_clipmax")
    bg_num = st.slider("How many unique backgrounds to generate?", 1, 10, 1, key="bg_num")
    bg_crossfade = st.checkbox("Crossfade between clips (0.3s fade)", value=True, key="bg_xfade")
    bg_output_size = st.selectbox(
        "Output size",
        ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"],
        key="bg_size"
    )
    allow_repeats = st.checkbox("Allow same clip to repeat in one background", value=False, key="bg_repeat")
    
    if st.button("🌄 Generate Backgrounds", type="primary", key="bg_btn"):
        if not bg_library:
            st.error("Please upload at least one clip.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = [int(x) for x in bg_output_size.split(" ")[0].split("x")]
            
            with st.spinner("Saving uploads..."):
                lib_paths = [save_upload(f, tmpdir, "lib") for f in bg_library]
            
            progress = st.progress(0)
            status = st.empty()
            results = []
            
            for v in range(bg_num):
                status.text(f"Generating background {v+1} of {bg_num}...")
                try:
                    selected = []
                    used = set()
                    accumulated = 0.0
                    
                    while accumulated < target_length:
                        if not allow_repeats:
                            available = [p for p in lib_paths if p not in used]
                            if not available:
                                used = set()
                                available = lib_paths
                        else:
                            available = lib_paths
                        
                        clip = random.choice(available)
                        used.add(clip)
                        clip_dur = get_duration(clip)
                        seg_len = random.uniform(clip_min, min(clip_max, clip_dur))
                        trimmed = trim_video(clip, seg_len, tmpdir)
                        selected.append(trimmed)
                        accumulated += get_duration(trimmed)
                    
                    job_id = uuid.uuid4().hex[:6]
                    output_path = os.path.join(tmpdir, f"bg_generated_{v+1}_{job_id}.mp4")
                    xfade = 0.3 if bg_crossfade else 0
                    concat_videos(selected, w, h, output_path, tmpdir, crossfade_seconds=xfade)
                    final_path = trim_video(output_path, target_length, tmpdir)
                    final_dur = get_duration(final_path)
                    
                    results.append((v+1, final_path, final_dur, len(selected)))
                except Exception as e:
                    st.error(f"Background {v+1} failed: {str(e)[:500]}")
                
                progress.progress((v+1)/bg_num)
            
            status.text(f"✅ Done — {len(results)} backgrounds generated.")
            
            for idx, path, dur, n_clips in results:
                with st.expander(f"Background {idx} ({dur:.1f}s, {n_clips} clips)", expanded=(idx == 1)):
                    with open(path, "rb") as f:
                        video_bytes = f.read()
                    st.video(video_bytes)
                    st.download_button(
                        f"⬇️ Download Background {idx}",
                        video_bytes,
                        file_name=f"background_{idx}.mp4",
                        mime="video/mp4",
                        key=f"bgdl_{idx}"
                    )

# ============== TAB 3: VARIATION GENERATOR (smart cuts) ==============

with tab3:
    st.header("Generate variations")
    st.write("Cuts land on natural speech pauses (between sentences and punch lines), not a fixed timer.")
    
    st.subheader("1. Upload your videos")
    hook_files = st.file_uploader(
        "Finished hook videos",
        type=["mp4", "mov"], accept_multiple_files=True, key="vg_hooks"
    )
    heygen_files = st.file_uploader(
        "HeyGen avatar videos with green screen",
        type=["mp4", "mov"], accept_multiple_files=True, key="vg_heygen"
    )
    bg_files = st.file_uploader(
        "Background videos",
        type=["mp4", "mov"], accept_multiple_files=True, key="vg_bg"
    )
    
    st.subheader("2. Settings")
    num_variations = st.slider("How many unique variations?", 1, 10, 3, key="vg_num")
    
    with st.expander("⚙️ Advanced cut settings"):
        min_pause = st.slider("Minimum pause length to count as a cut point (seconds)", 0.10, 1.0, 0.25, 0.05, key="vg_minpause",
                              help="Shorter = more sensitive, more cuts. Longer = only major pauses become cuts.")
        silence_db = st.slider("Silence threshold (dB)", -50, -15, -30, 1, key="vg_db",
                               help="Lower (more negative) = stricter silence detection. -30 works for most HeyGen voices.")
        fallback_min = st.slider("Fallback minimum cut (if no pauses found)", 1.0, 5.0, 2.0, 0.5, key="vg_fmin")
        fallback_max = st.slider("Fallback maximum cut (if no pauses found)", 2.0, 8.0, 3.5, 0.5, key="vg_fmax")
    
    vg_output_size = st.selectbox(
        "Output size",
        ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"],
        key="vg_size"
    )
    
    if st.button("🎬 Generate Variations", type="primary", key="vg_btn"):
        if not hook_files or not heygen_files or not bg_files:
            st.error("Please upload at least one hook, one HeyGen video, and one background.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = [int(x) for x in vg_output_size.split(" ")[0].split("x")]
            
            with st.spinner("Saving uploads..."):
                hook_paths = [(f.name, save_upload(f, tmpdir, "hook")) for f in hook_files]
                heygen_paths = [(f.name, save_upload(f, tmpdir, "heygen")) for f in heygen_files]
                bg_paths = [(f.name, save_upload(f, tmpdir, "bg")) for f in bg_files]
            
            progress = st.progress(0)
            status = st.empty()
            results = []
            
            for i in range(num_variations):
                status.text(f"Generating variation {i+1} of {num_variations}...")
                try:
                    hook_name, hook = random.choice(hook_paths)
                    hg_name, heygen = random.choice(heygen_paths)
                    bg_name, bg = random.choice(bg_paths)
                    
                    job_id = uuid.uuid4().hex[:6]
                    main_path = os.path.join(tmpdir, f"main_{job_id}.mp4")
                    final_path = os.path.join(tmpdir, f"final_v{i+1}_{job_id}.mp4")
                    
                    n_cuts = composite_main_smart_cuts(
                        bg, heygen, w, h, main_path,
                        min_pause=min_pause,
                        silence_db=silence_db,
                        fallback_min_cut=fallback_min,
                        fallback_max_cut=fallback_max
                    )
                    concat_videos([hook, main_path], w, h, final_path, tmpdir)
                    
                    results.append((i+1, final_path, hook_name, hg_name, bg_name, n_cuts))
                except Exception as e:
                    st.error(f"Variation {i+1} failed: {str(e)[:500]}")
                
                progress.progress((i+1)/num_variations)
            
            status.text(f"✅ Done — {len(results)} variations generated.")
            
            for idx, path, hook_name, hg_name, bg_name, n_cuts in results:
                with st.expander(f"Variation {idx} ({n_cuts} cuts)", expanded=(idx == 1)):
                    st.caption(f"Hook: {hook_name} | HeyGen: {hg_name} | Background: {bg_name}")
                    with open(path, "rb") as f:
                        video_bytes = f.read()
                    st.video(video_bytes)
                    st.download_button(
                        f"⬇️ Download Variation {idx}",
                        video_bytes,
                        file_name=f"variation_{idx}.mp4",
                        mime="video/mp4",
                        key=f"dl_{idx}"
                    )
