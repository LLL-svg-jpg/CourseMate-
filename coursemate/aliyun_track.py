"""智慧职教阿里云拼图轨迹；只保留实测使用的 feedback 分支。

改编自 https://github.com/kangleyao/slider-captcha-lab/blob/main/human_track.py
Copyright (c) 2026 slider-captcha-lab contributors. MIT License.
完整许可见 THIRD_PARTY_NOTICES.txt。
"""
import math
import random

import numpy as np


def _time_grid(total_s, n, rng):
    raw = []
    for _ in range(n - 1):
        r = rng.random()
        if r < 0.62:
            raw.append(rng.uniform(0.0035, 0.0065))
        elif r < 0.78:
            raw.append(rng.uniform(0.008, 0.018))
        elif r < 0.9:
            raw.append(rng.uniform(0.025, 0.06))
        else:
            raw.append(rng.uniform(0.07, 0.22))
    scale = total_s / max(sum(raw), 1e-09)
    min_dt_s = 0.0012
    if scale * 0.004 < min_dt_s:
        times = [0.0]
        for dt in raw:
            dt_s = max(dt * scale, min_dt_s)
            times.append(times[-1] + dt_s)
        max_end = total_s * 1.5
        if times[-1] > max_end:
            k = max_end / times[-1]
            times = [t * k for t in times]
        times[-1] = max(times[-1], total_s)
    else:
        times = [0.0]
        for dt in raw:
            times.append(times[-1] + dt * scale)
        times[-1] = total_s
    return times


def aliyun_track(distance):
    """仅生成智慧职教阿里云拼图的鼠标增量与时间间隔。"""
    rng = random
    events = rng.randint(130, 200)
    decay = rng.uniform(4.0, 12.0)
    front_ramp = rng.uniform(0.08, 0.25)
    y_amplitude = rng.uniform(8.0, 18.0)
    if rng.random() < 0.75:
        y_drift = -rng.uniform(25.0, 90.0)
    else:
        y_drift = rng.uniform(10.0, 35.0)
    y_cycles = rng.uniform(0.5, 1.1)
    D = float(distance)
    D_actual = D

    def v_unit(u):
        if u < front_ramp and front_ramp > 0:
            return u / front_ramp
        uu = (u - front_ramp) / max(1e-06, 1.0 - front_ramp)
        return (1.0 - uu) ** decay
    M = 400
    grid_u = np.linspace(0.0, 1.0, M)
    Vn = np.array([v_unit(u) for u in grid_u])
    Sn = np.zeros(M)
    for i in range(1, M):
        Sn[i] = Sn[i - 1] + (Vn[i - 1] + Vn[i]) * 0.5 * (grid_u[i] - grid_u[i - 1])
    Vn_sum = float(Sn[-1])
    r = rng.random()
    if r < 0.2:
        total_s = rng.uniform(0.9, 1.25)
    elif r < 0.65:
        total_s = rng.uniform(1.25, 1.9)
    else:
        total_s = rng.uniform(1.9, 3.1)
    times = _time_grid(total_s, events, rng)
    points = []
    for t in times:
        u = min(1.0, t / total_s) if total_s > 0 else 1.0
        x = D_actual * float(np.interp(u, grid_u, Sn)) / Vn_sum
        phase = 2 * math.pi * y_cycles * u
        envelope = math.sin(math.pi * u) ** 1.7
        y = y_drift * u + y_amplitude * envelope * math.sin(phase)
        points.append((x, y, t))
    track = []
    for i in range(1, len(points)):
        x0, y0, t0 = points[i - 1]
        x1, y1, t1 = points[i]
        dx = x1 - x0
        if dx < 0.01:
            dx = 0.01
        track.append((dx, y1 - y0, (t1 - t0) * 1000.0))
    cur = sum((dx for dx, _, _ in track))
    if abs(cur - D_actual) > 1e-09:
        n_main = max(2, int(len(track) * 0.8))
        main_sum = sum((track[i][0] for i in range(n_main)))
        if main_sum > 1.0:
            k = (main_sum + (D_actual - cur)) / main_sum
            for i in range(n_main):
                dx, dy, dt = track[i]
                track[i] = (dx * k, dy, dt)
    live = len(track)
    while live > 10 and track[live - 1][0] <= 0.05:
        live -= 1
    dead_sum = sum((t[0] for t in track[live:]))
    dead_dy = sum((t[1] for t in track[live:]))
    dead_dt = sum((t[2] for t in track[live:]))
    if live < len(track) and dead_dt > 0:
        dx, dy, dt = track[live - 1]
        track[live - 1] = (dx, dy, dt + dead_dt)
    track = track[:live]
    if dead_sum > 1e-09:
        cur = sum((t[0] for t in track))
        if cur > 0.01:
            k_red = (cur + dead_sum) / cur
            track = [(max(0.01, dx * k_red), dy, dt) for dx, dy, dt in track]
    if abs(dead_dy) > 1e-09 and track:
        dx, dy, dt = track[-1]
        track[-1] = (dx, dy + dead_dy, dt)
    n = len(track)
    if n >= 10 and sum((t[0] for t in track)) > 10:
        split = int(n * rng.uniform(0.45, 0.6))
        D_actual_now = sum((t[0] for t in track))
        front_budget = D_actual_now * rng.uniform(0.15, 0.3)
        front = track[:split]
        front_sum = sum((t[0] for t in front))
        if front_sum > 1.0:
            k_front = front_budget / front_sum
            front = [(max(0.01, dx * k_front), dy, dt) for dx, dy, dt in front]
        track[:split] = front
        flick_total = D_actual_now - front_budget
        back_dy_total = sum((t[1] for t in track[split:]))
        if flick_total > 5.0 and n - split >= 6:
            valley_n = rng.randint(3, 6)
            flick_n = rng.randint(16, 28)
            coast_n = rng.randint(5, 10)
            valley_share = rng.uniform(0.02, 0.05)
            flick_share = rng.uniform(0.86, 0.94)
            valley_dx = flick_total * valley_share
            flick_dx = flick_total * flick_share
            coast_dx = flick_total - valley_dx - flick_dx
            v_frames = [(valley_dx / valley_n, 0.0, rng.uniform(40, 110)) for _ in range(valley_n)]
            f_shape = []
            pk = rng.uniform(0.35, 0.6)
            sigma = rng.uniform(0.07, 0.11)
            for j in range(flick_n):
                ph = j / max(1, flick_n - 1)
                s = math.exp(-(ph - pk) ** 2 / (2 * sigma ** 2))
                f_shape.append(0.05 + s)
            fs = sum(f_shape)
            f_frames = [(flick_dx * s / fs, 0.0, rng.uniform(2.5, 6.0)) for s in f_shape]
            c_frames = [(coast_dx / coast_n, 0.0, rng.uniform(6.0, 16.0)) for _ in range(coast_n)]
            cs = sum((f[0] for f in c_frames))
            if cs > 0.01:
                c_frames = [(d * coast_dx / cs, 0.0, dt) for d, _, dt in c_frames]
            new_back = v_frames + f_frames + c_frames
            tot_new = sum((t[0] for t in new_back))
            for j, (dx, _, dt) in enumerate(new_back):
                new_back[j] = (max(0.01, dx), back_dy_total * dx / max(tot_new, 1e-09), dt)
            track[split:] = new_back
            n = len(track)
        grid = rng.uniform(0.42, 0.58)
        quantized = []
        for dx, dy, dt in track:
            q = round(dx / grid) * grid
            if q > 0.5 and rng.random() < 0.35:
                q = q + rng.choice((-1, 1)) * grid
            quantized.append(max(0.01, q))
        q_sum = sum(quantized)
        orig = sum((t[0] for t in track))
        residual_q = orig - q_sum
        if abs(residual_q) > 1e-09 and q_sum > 0.01:
            for i in range(n):
                share = quantized[i] / q_sum
                quantized[i] = max(0.01, quantized[i] + residual_q * share)
            still = orig - sum(quantized)
            if abs(still) > 1e-09:
                imax = max(range(n), key=lambda i: quantized[i])
                quantized[imax] += still
        for i in range(n):
            dx, dy, dt = track[i]
            track[i] = (quantized[i], dy, dt)
        n_zero = max(1, int(n * rng.uniform(0.03, 0.07)))
        for _ in range(n_zero):
            zi = rng.randint(2, n - 3)
            dx, dy, dt = track[zi]
            if dx > 0.05:
                nx = track[zi + 1]
                track[zi] = (0.0, dy, dt)
                track[zi + 1] = (nx[0] + dx, nx[1], nx[2])
        for _ in range(rng.randint(1, 3)):
            ri = rng.randint(int(n * 0.25), max(int(n * 0.25) + 2, int(n * 0.8)))
            if ri + 3 >= n:
                continue
            pull = rng.uniform(0.8, 2.2)
            track[ri] = (track[ri][0] - pull * 0.6, track[ri][1], track[ri][2])
            track[ri + 1] = (track[ri + 1][0] - pull * 0.4, track[ri + 1][1], track[ri + 1][2])
            track[ri + 2] = (track[ri + 2][0] + pull, track[ri + 2][1], track[ri + 2][2])
        r_p = rng.random()
        n_pauses = 0 if r_p < 0.4 else 1 if r_p < 0.8 else 2
        for _ in range(n_pauses):
            i = rng.randint(int(n * 0.4), max(int(n * 0.4) + 1, int(n * 0.85)))
            dx, dy, dt = track[i]
            pause_ms = rng.uniform(60, 160)
            track[i] = (dx, dy, dt + pause_ms)
        if rng.random() < 0.55:
            back_n = rng.randint(6, 13)
            if n - back_n > 10:
                back_total = rng.uniform(3.0, 12.0)
                weights = [1.0 + math.sin(math.pi * (k + 1) / back_n) for k in range(back_n)]
                w_sum = sum(weights)
                backs = [back_total * w / w_sum for w in weights]
                main_frames = track[:n - back_n]
                main_sum = sum((t[0] for t in main_frames))
                need = D + back_total
                if main_sum > 1.0 and need > 0:
                    k_scale = need / main_sum
                    for i in range(n - back_n):
                        dx, dy, dt = track[i]
                        track[i] = (max(0.01, dx * k_scale), dy, dt)
                back_frames = []
                dy_back_total = sum((track[n - back_n + k][1] for k in range(back_n)))
                dy_back_w = [abs(track[n - back_n + k][1]) + 0.01 for k in range(back_n)]
                dw_sum = sum(dy_back_w)
                for k in range(back_n):
                    dx, dy, dt = track[n - back_n + k]
                    dt_new = rng.uniform(40, 140) if k == 0 else rng.uniform(3.5, 10.0)
                    dy_new = dy_back_total * dy_back_w[k] / dw_sum if dw_sum > 0 else 0.0
                    back_frames.append((-backs[k], dy_new, dt_new))
                track[n - back_n:] = back_frames
                final_sum = sum((t[0] for t in track))
                residual = D - final_sum
                if abs(residual) > 1e-09:
                    wts = [max(t[0], 0.01) for t in track[:n - back_n]]
                    wsum = sum(wts)
                    if wsum > 0.01:
                        for i in range(n - back_n):
                            dx, dy, dt = track[i]
                            track[i] = (max(0.01, dx + residual * wts[i] / wsum), dy, dt)
    n = len(track)
    rem = 0.2
    win_start = n
    for i in range(n - 1, -1, -1):
        rem -= track[i][2] / 1000.0
        if rem <= 0:
            win_start = i
            break
    snap_diff = 0.0
    for i in range(win_start, n):
        dx, dy, dt = track[i]
        if 0 < abs(dx) < 1.0:
            if rng.random() < 0.6:
                snap_diff -= dx
                track[i] = (0.0, dy, dt)
            else:
                sign = 1.0 if dx > 0 else -1.0
                snap_diff += sign * 1.15 - dx
                track[i] = (sign * 1.15, dy, dt)
    if abs(snap_diff) > 1e-09:
        for i in range(win_start - 1, -1, -1):
            dx, dy, dt = track[i]
            if dx >= 1.0:
                track[i] = (max(0.01, dx + snap_diff), dy, dt)
                break
        else:
            dx, dy, dt = track[0]
            track[0] = (max(0.01, dx + snap_diff), dy, dt)
    final_diff = D - sum((t[0] for t in track))
    if abs(final_diff) > 1e-09:
        wts2 = [t[0] if t[0] >= 1.0 else 0.0 for t in track]
        ws2 = sum(wts2)
        if ws2 > 0.5:
            for i in range(len(track)):
                if wts2[i] > 0:
                    dx, dy, dt = track[i]
                    track[i] = (max(0.01, dx + final_diff * wts2[i] / ws2), dy, dt)
        else:
            dx, dy, dt = track[0]
            track[0] = (max(0.01, dx + final_diff), dy, dt)
    return track
