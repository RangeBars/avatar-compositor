import streamlit as st
import subprocess
import random
import os
import tempfile
import uuid

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

def composite_main(bg_path, avatar_path, w, h, min_cut, max_cut, output_path):
    duration = get_duration(avatar_path)
    zones = [
        (0.03, 0.05, 0.40),
        (0.57, 0.05, 0.40),
        (0.03, 0.55, 0.45),
        (0.55, 0.55, 0.45),
        (0.30, 0.30, 0.40),
        (0.03, 0.30, 0.45),
        (0.55, 0.30, 0.45),
        (0.20, 0.50, 0.55),
    ]
    segments = []
    t = 0.0
    last_zone = None
    while t < duration:
        seg_len = random.uniform(min_cut, max_cut)
        end = min(t + seg_len, duration)
        available = [z for z in zones if z != last_zone]
        zone = random.choice(available)
        last_zone = zone
        x = int(w * zone[0])
        y = int(h * zone[1])
        segments.append((t, end, x, y, zone[2]))
        t = end
    
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

# ============== TAB 1: HOOK BUILDER ==============

with tab1:
    st.header("Build a finished hook")
    st.write("Combine a hook clip with an end scene into one ready-to-use hook file.")
    
    hook_clip = st.file_uploader("Hook clip (the opener)", type=["mp4", "mov"], key="hb_hook")
    end_scene = st.file_uploader("End scene (plays right after the hook)", type=["mp4", "mov"], key="hb_end")
    hb_output_size = st.selectbox(
        "Output size",
        ["1080x1920 (vertical)", "1920x1080 (horizontal)", "1080x1080 (square)"],
        key="hb_size"
    )
    hb_crossfade = st.checkbox("Crossfade between hook and end scene (0.3s fade)", value=False, key="hb_xfade")
    
    if st.button("🪝 Build Hook", type="primary", key="hb_btn"):
        if not hook_clip or not end_scene:
            st.error("Please upload both the hook clip and the end scene.")
        else:
            tmpdir = tempfile.gettempdir()
            w, h = [int(x) for x in hb_output_size.split(" ")[0].split("x")]
            
            with st.spinner("Building hook..."):
                try:
                    hook_path = save_upload(hook_clip, tmpdir, "rawhook")
                    end_path = save_upload(end_scene, tmpdir, "rawend")
                    job_id = uuid.uuid4().hex[:6]
                    output_path = os.path.join(tmpdir, f"hook_finished_{job_id}.mp4")
                    
                    xfade = 0.3 if hb_crossfade else 0
                    concat_videos([hook_path, end_path], w, h, output_path, tmpdir, crossfade_seconds=xfade)
                    
                    total_dur = get_duration(output_path)
                    st.success(f"✅ Hook built — total length: {total_dur:.1f} seconds")
                    
                    with open(output_path, "rb") as f:
                        video_bytes = f.read()
                    st.video(video_bytes)
                    st.download_button(
                        "⬇️ Download Finished Hook",
                        video_bytes,
                        file_name=f"hook_{job_id}.mp4",
                        mime="video/mp4",
                        key="hb_download"
                    )
                    st.info("💡 Save this — upload it as a hook in the Variation Generator tab.")
                except Exception as e:
                    st.error(f"Build failed: {str(e)[:1000]}")

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
        elif not allow_repeats and len(bg_library) < 3:
            st.warning("With repeats off, you'll likely run out of clips fast. Upload more clips or enable repeats.")
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

# ============== TAB 3: VARIATION GENERATOR ==============

with tab3:
    st.header("Generate variations")
    st.write("Combine finished hooks + HeyGen avatar + backgrounds into unique videos.")
    
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
        "Background videos (use ones from Background Generator tab)",
        type=["mp4", "mov"], accept_multiple_files=True, key="vg_bg"
    )
    
    st.subheader("2. Settings")
    num_variations = st.slider("How many unique variations?", 1, 10, 3, key="vg_num")
    min_cut = st.slider("Shortest avatar cut (seconds)", 1.0, 5.0, 2.0, 0.5, key="vg_mincut")
    max_cut = st.slider("Longest avatar cut (seconds)", 2.0, 8.0, 3.5, 0.5, key="vg_maxcut")
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
                    
                    composite_main(bg, heygen, w, h, min_cut, max_cut, main_path)
                    concat_videos([hook, main_path], w, h, final_path, tmpdir)
                    
                    results.append((i+1, final_path, hook_name, hg_name, bg_name))
                except Exception as e:
                    st.error(f"Variation {i+1} failed: {str(e)[:500]}")
                
                progress.progress((i+1)/num_variations)
            
            status.text(f"✅ Done — {len(results)} variations generated.")
            
            for idx, path, hook_name, hg_name, bg_name in results:
                with st.expander(f"Variation {idx}", expanded=(idx == 1)):
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
