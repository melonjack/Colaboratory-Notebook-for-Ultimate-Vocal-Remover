import os

import librosa
import numpy as np
import soundfile as sf
import math
import json
import hashlib
import threading

def loadWave(inp, mp, hep='none'):
    X_wave, X_spec_s = {},{}
    bands_n = len(mp.param['band'])-1
    X_wave[bands_n+1], _ = librosa.load(
        inp, mp.param['band'][bands_n+1]['sr'], False, dtype=np.float32, res_type=mp.param['band'][bands_n+1]['res_type'])
    if X_wave[bands_n+1].ndim == 1:
        X_wave[bands_n+1] = np.asarray([X_wave[bands_n+1], X_wave[bands_n+1]])
    X_spec_s[bands_n+1] = wave_to_spectrogram(X_wave[bands_n+1], mp.param['band'][bands_n+1]['hl'], mp.param['band'][bands_n+1]['n_fft'], mp, True)
    if hep != 'none':
        input_high_end_h = (mp.param['band'][bands_n+1]['n_fft']//2 - mp.param['band'][bands_n+1]['crop_stop']) + (mp.param['pre_filter_stop'] - mp.param['pre_filter_start'])
        input_high_end = X_spec_s[bands_n+1][:, mp.param['band'][bands_n+1]['n_fft']//2-input_high_end_h:mp.param['band'][bands_n+1]['n_fft']//2, :]
    else:
        input_high_end_h = input_high_end = None
    for d in range(bands_n, 0, -1):
        bp = mp.param['band'][d]
        X_wave[d] = librosa.resample(X_wave[d+1], mp.param['band'][d+1]['sr'], bp['sr'], res_type=bp['res_type'])
        X_spec_s[d] = wave_to_spectrogram(X_wave[d], bp['hl'], bp['n_fft'], mp, True) # threading true
    X_spec_m = combine_spectrograms(X_spec_s, mp)
    del X_wave, X_spec_s
    return X_spec_m, input_high_end_h, input_high_end

def spec_effects(mp, inp, o, algorithm='invert'):
    X_spec_m,_,_ = loadWave(inp[0], mp)
    y_spec_m,_,_ = loadWave(inp[1], mp)
    c = min(X_spec_m.shape[2],y_spec_m.shape[2])
    X_spec_m,y_spec_m = X_spec_m[...,:c], y_spec_m[...,:c]
    if algorithm == 'invert':
        y_spec_m = reduce_vocal_aggressively(X_spec_m, y_spec_m, 0.2)
        v_spec_m = X_spec_m - y_spec_m
    if algorithm == 'min_mag':
        v_spec_m = np.where(np.abs(X_spec_m) <= np.abs(y_spec_m), X_spec_m, y_spec_m)
    if algorithm == 'max_mag':
        v_spec_m = np.where(np.abs(X_spec_m) >= np.abs(y_spec_m), X_spec_m, y_spec_m)
    if algorithm == 'comb_norm': # debug
        v_spec_m = X_spec_m + y_spec_m
    if algorithm == 'mul':
        s1 = y_spec_m * X_spec_m
        s2 = .5*(y_spec_m+X_spec_m)
        v_spec_m = np.divide(s1, s2, out=np.zeros_like(s1), where=s2!=0)
    if algorithm == 'crossover':
        freq_to_bin = 2*c/44100
        bs = int(500*freq_to_bin)
        be = int(14000 * freq_to_bin)
        v_spec_m = y_spec_m * get_lp_filter_mask(c, bs, be)+X_spec_m*get_hp_filter_mask(c, be, bs)
    wave = cmb_spectrogram_to_wave(v_spec_m, mp)
    if algorithm == 'comb_norm':
        wave = normalise(wave)
    sf.write('{}.wav'.format(o), wave, mp.param['sr'])


def normalise(wave):
    return wave / max(np.max(wave), abs(np.min(wave)))

def crop_center(h1, h2):
    h1_shape = h1.size()
    h2_shape = h2.size()

    if h1_shape[3] == h2_shape[3]:
        return h1
    elif h1_shape[3] < h2_shape[3]:
        raise ValueError('h1_shape[3] must be greater than h2_shape[3]')

    # s_freq = (h2_shape[2] - h1_shape[2]) // 2
    # e_freq = s_freq + h1_shape[2]
    s_time = (h1_shape[3] - h2_shape[3]) // 2
    e_time = s_time + h2_shape[3]
    h1 = h1[:, :, :, s_time:e_time]

    return h1


def wave_to_spectrogram(wave, hop_length, n_fft, mp, multithreading):
    if mp.param['reverse']:
        wave_left = np.flip(np.asfortranarray(wave[0]))
        wave_right = np.flip(np.asfortranarray(wave[1]))
    elif mp.param['mid_side_b']:
        wave_left = np.asfortranarray(np.add(wave[0], wave[1] * .5))
        wave_right = np.asfortranarray(np.subtract(wave[1], wave[0] * .5))    
    elif mp.param['mid_side_b2']:
        wave_left = np.asfortranarray(np.add(wave[1], wave[0] * .5))
        wave_right = np.asfortranarray(np.subtract(wave[0], wave[1] * .5))
    elif mp.param['mid_side']:
        wave_left = np.asfortranarray(np.add(wave[0], wave[1]) / 2)
        wave_right = np.asfortranarray(np.subtract(wave[0], wave[1]))    
    elif mp.param['stereo_w']:
        wave_left = np.asfortranarray(np.subtract(wave[0], wave[1] * .25))
        wave_right = np.asfortranarray(np.subtract(wave[1], wave[0] * .25))
    else:
        wave_left = np.asfortranarray(wave[0])
        wave_right = np.asfortranarray(wave[1])
      
    if multithreading:
        def run_thread(**kwargs):
            global spec_left_mt
            spec_left_mt = librosa.stft(**kwargs)

        thread = threading.Thread(target=run_thread, kwargs={'y': wave_left, 'n_fft': n_fft, 'hop_length': hop_length})
        thread.start()
        spec_right = librosa.stft(wave_right, n_fft, hop_length=hop_length)
        thread.join()
        spec = np.asfortranarray([spec_left_mt, spec_right])
    else:
        spec_left = librosa.stft(wave_left, n_fft, hop_length=hop_length)
        spec_right = librosa.stft(wave_right, n_fft, hop_length=hop_length)
        spec = np.asfortranarray([spec_left, spec_right])

    return spec
    
    
def combine_spectrograms(specs, mp):
    l = min([specs[i].shape[2] for i in specs])    
    spec_c = np.zeros(shape=(2, mp.param['bins'] + 1, l), dtype=np.complex64)
    offset = 0
    bands_n = len(mp.param['band'])
    
    for d in range(1, bands_n + 1):
        h = mp.param['band'][d]['crop_stop'] - mp.param['band'][d]['crop_start']
        spec_c[:, offset:offset+h, :l] = specs[d][:, mp.param['band'][d]['crop_start']:mp.param['band'][d]['crop_stop'], :l]
        offset += h
        
    if offset > mp.param['bins']:
        raise ValueError('Too much bins')
        
    # lowpass fiter
    if mp.param['pre_filter_start'] > 0: # and mp.param['band'][bands_n]['res_type'] in ['scipy', 'polyphase']:   
        if bands_n == 1:
            spec_c = fft_lp_filter(spec_c, mp.param['pre_filter_start'], mp.param['pre_filter_stop'])
        else:
            gp = 1        
            for b in range(mp.param['pre_filter_start'] + 1, mp.param['pre_filter_stop']):
                g = math.pow(10, -(b - mp.param['pre_filter_start']) * (3.5 - gp) / 20.0)
                gp = g
                spec_c[:, b, :] *= g

    return np.asfortranarray(spec_c)
    

def spectrogram_to_image(spec, mode='magnitude'):
    if mode == 'magnitude':
        if np.iscomplexobj(spec):
            y = np.abs(spec)
        else:
            y = spec
        y = np.log10(y ** 2 + 1e-8)
    elif mode == 'phase':
        if np.iscomplexobj(spec):
            y = np.angle(spec)
        else:
            y = spec

    y -= y.min()
    y *= 255 / y.max()
    img = np.uint8(y)

    if y.ndim == 3:
        img = img.transpose(1, 2, 0)
        img = np.concatenate([
            np.max(img, axis=2, keepdims=True), img
        ], axis=2)

    return img


def reduce_vocal_aggressively(X, y, softmask):
    v = X - y
    y_mag_tmp = np.abs(y)
    v_mag_tmp = np.abs(v)

    v_mask = v_mag_tmp > y_mag_tmp
    y_mag = np.clip(y_mag_tmp - v_mag_tmp * v_mask * softmask, 0, np.inf)

    return y_mag * np.exp(1.j * np.angle(y))


def mask_silence(mag, ref, thres=0.2, min_range=64, fade_size=32):
    if min_range < fade_size * 2:
        raise ValueError('min_range must be >= fade_area * 2')

    mag = mag.copy()

    idx = np.where(ref.mean(axis=(0, 1)) < thres)[0]
    starts = np.insert(idx[np.where(np.diff(idx) != 1)[0] + 1], 0, idx[0])
    ends = np.append(idx[np.where(np.diff(idx) != 1)[0]], idx[-1])
    uninformative = np.where(ends - starts > min_range)[0]
    if len(uninformative) > 0:
        starts = starts[uninformative]
        ends = ends[uninformative]
        old_e = None
        for s, e in zip(starts, ends):
            if old_e is not None and s - old_e < fade_size:
                s = old_e - fade_size * 2

            if s != 0:
                weight = np.linspace(0, 1, fade_size)
                mag[:, :, s:s + fade_size] += weight * ref[:, :, s:s + fade_size]
            else:
                s -= fade_size

            if e != mag.shape[2]:
                weight = np.linspace(1, 0, fade_size)
                mag[:, :, e - fade_size:e] += weight * ref[:, :, e - fade_size:e]
            else:
                e += fade_size

            mag[:, :, s + fade_size:e - fade_size] += ref[:, :, s + fade_size:e - fade_size]
            old_e = e

    return mag

def align_wave_head_and_tail(a, b):
    l = min([a[0].size, b[0].size])
    
    return a[:l,:l], b[:l,:l]
    

def cache_or_load(mix_path, inst_path, mp):
    mix_basename = os.path.splitext(os.path.basename(mix_path))[0]
    inst_basename = os.path.splitext(os.path.basename(inst_path))[0]

    cache_dir = 'mph{}'.format(hashlib.sha1(json.dumps(mp.param, sort_keys=True).encode('utf-8')).hexdigest())
    #mix_cache_dir = os.path.join(os.path.dirname(mix_path), cache_dir)
    #inst_cache_dir = os.path.join(os.path.dirname(inst_path), cache_dir)
    mix_cache_dir = os.path.join('cache', cache_dir)
    inst_cache_dir = os.path.join('cache', cache_dir)

    os.makedirs(mix_cache_dir, exist_ok=True)
    os.makedirs(inst_cache_dir, exist_ok=True)

    mix_cache_path = os.path.join(mix_cache_dir, mix_basename + '.npy')
    inst_cache_path = os.path.join(inst_cache_dir, inst_basename + '.npy')

    if os.path.exists(mix_cache_path) and os.path.exists(inst_cache_path):
        X_spec_m = np.load(mix_cache_path)
        y_spec_m = np.load(inst_cache_path)
    else:
        X_wave, y_wave, X_spec_s, y_spec_s = {}, {}, {}, {}
         
        for d in range(len(mp.param['band']), 0, -1):            
            bp = mp.param['band'][d]
                    
            if d == len(mp.param['band']): # high-end band
                X_wave[d], _ = librosa.load(
                    mix_path, bp['sr'], False, dtype=np.float32, res_type=bp['res_type'])
                y_wave[d], _ = librosa.load(
                    inst_path, bp['sr'], False, dtype=np.float32, res_type=bp['res_type'])
            else: # lower bands
                X_wave[d] = librosa.resample(X_wave[d+1], mp.param['band'][d+1]['sr'], bp['sr'], res_type=bp['res_type'])
                y_wave[d] = librosa.resample(y_wave[d+1], mp.param['band'][d+1]['sr'], bp['sr'], res_type=bp['res_type'])
            
            X_wave[d], y_wave[d] = align_wave_head_and_tail(X_wave[d], y_wave[d])
            
            X_spec_s[d] = wave_to_spectrogram(X_wave[d], bp['hl'], bp['n_fft'], mp.param['mid_side'], mp.param['reverse'])
            y_spec_s[d] = wave_to_spectrogram(y_wave[d], bp['hl'], bp['n_fft'], mp.param['mid_side'], mp.param['reverse'])
            
        del X_wave, y_wave
                 
        X_spec_m = combine_spectrograms(X_spec_s, mp)
        y_spec_m = combine_spectrograms(y_spec_s, mp)
        
        if X_spec_m.shape != y_spec_m.shape:
            raise ValueError('The combined spectrograms are different: ' + mix_path)

        _, ext = os.path.splitext(mix_path)

        np.save(mix_cache_path, X_spec_m)
        np.save(inst_cache_path, y_spec_m)

    return X_spec_m, y_spec_m


def spectrogram_to_wave(spec, hop_length, mp, multithreading):
    import threading

    spec_left = np.asfortranarray(spec[0])
    spec_right = np.asfortranarray(spec[1])
    
    if multithreading:
        def run_thread(**kwargs):
            global wave_left
            wave_left = librosa.istft(**kwargs)
            
        thread = threading.Thread(target=run_thread, kwargs={'stft_matrix': spec_left, 'hop_length': hop_length})
        thread.start()
        wave_right = librosa.istft(spec_right, hop_length=hop_length)
        thread.join()   
    else:
        wave_left = librosa.istft(spec_left, hop_length=hop_length)
        wave_right = librosa.istft(spec_right, hop_length=hop_length)
    
    if mp.param['reverse']:
        return np.asfortranarray([np.flip(wave_left), np.flip(wave_right)])
    elif mp.param['mid_side_b']:
        return np.asfortranarray([np.subtract(wave_left / 1.25, .4 * wave_right), np.add(wave_right / 1.25, .4 * wave_left)])       
    elif mp.param['mid_side_b2']:
        return np.asfortranarray([np.add(wave_right / 1.25, .4 * wave_left), np.subtract(wave_left / 1.25, .4 * wave_right)])    
    elif mp.param['mid_side']:
        return np.asfortranarray([np.add(wave_left, wave_right / 2), np.subtract(wave_left, wave_right / 2)])
    elif mp.param['stereo_w']:
        return np.asfortranarray([np.add(wave_left, wave_right * .25) / 0.9375, np.add(wave_right, wave_left * .25) / 0.9375])
    else:
        return np.asfortranarray([wave_left, wave_right])
    
    
def cmb_spectrogram_to_wave(spec_m, mp, extra_bins_h=None, extra_bins=None):
    wave_band = {}
    bands_n = len(mp.param['band'])    
    offset = 0

    for d in range(1, bands_n + 1):
        bp = mp.param['band'][d]
        spec_s = np.zeros(shape=(2, bp['n_fft'] // 2 + 1, spec_m.shape[2]), dtype=complex)
        h = bp['crop_stop'] - bp['crop_start']
        spec_s[:, bp['crop_start']:bp['crop_stop'], :] = spec_m[:, offset:offset+h, :]
        
        offset += h
        if d == bands_n: # higher
            if extra_bins_h: # if --high_end_process bypass
                max_bin = bp['n_fft'] // 2
                spec_s[:, max_bin-extra_bins_h:max_bin, :] = extra_bins[:, :extra_bins_h, :]
            if bp['hpf_start'] > 0:
                spec_s = fft_hp_filter(spec_s, bp['hpf_start'], bp['hpf_stop'] - 1)
            if bands_n == 1:
                wave = spectrogram_to_wave(spec_s, bp['hl'], mp, False)
            else:
                wave = np.add(wave, spectrogram_to_wave(spec_s, bp['hl'], mp, False))
        else:
            sr = mp.param['band'][d+1]['sr']
            if d == 1: # lower
                spec_s = fft_lp_filter(spec_s, bp['lpf_start'], bp['lpf_stop'])
                wave = librosa.resample(spectrogram_to_wave(spec_s, bp['hl'], mp, False), bp['sr'], sr, res_type="sinc_fastest")
            else: # mid
                spec_s = fft_hp_filter(spec_s, bp['hpf_start'], bp['hpf_stop'] - 1)
                spec_s = fft_lp_filter(spec_s, bp['lpf_start'], bp['lpf_stop'])
                wave2 = np.add(wave, spectrogram_to_wave(spec_s, bp['hl'], mp, False))
                wave = librosa.resample(wave2, bp['sr'], sr, res_type="sinc_fastest")
        
    return wave.T
def cmb_spectrogram_to_wave_ffmpeg(spec_m, mp, tmp_basename, extra_bins_h=None, extra_bins=None):
    import subprocess

    bands_n = len(mp.param['band'])    
    offset = 0
    ffmprc = {}

    for d in range(1, bands_n + 1):
        bp = mp.param['band'][d]
        spec_s = np.ndarray(shape=(2, bp['n_fft'] // 2 + 1, spec_m.shape[2]), dtype=complex)
        h = bp['crop_stop'] - bp['crop_start']
        spec_s[:, bp['crop_start']:bp['crop_stop'], :] = spec_m[:, offset:offset+h, :]
        tmp_wav = '{}_cmb_spectrogram_to_wave_b{}_sr{}'.format(tmp_basename, d, str(bp['sr']) + '.wav')
        tmp_wav2 = '{}_cmb_spectrogram_to_wave_b{}_sr{}'.format(tmp_basename, d, str(mp.param['sr']) + '.wav')
        
        offset += h
        if d == bands_n: # high-end
            if extra_bins_h: # if --high_end_process bypass
                max_bin = bp['n_fft'] // 2
                spec_s[:, max_bin-extra_bins_h:max_bin, :] = extra_bins[:, :extra_bins_h, :]
            if bp['hpf_start'] > 0:
                spec_s = fft_hp_filter(spec_s, bp['hpf_start'], bp['hpf_stop'] - 1)
            if bands_n == 1:
                wave = spectrogram_to_wave(spec_s, bp['hl'], mp, False)
            else:
                wave = spectrogram_to_wave(spec_s, bp['hl'], mp, False)
        else:
            if d == 1: # lower
                spec_s = fft_lp_filter(spec_s, bp['lpf_start'], bp['lpf_stop'])
            else: # mid
                spec_s = fft_hp_filter(spec_s, bp['hpf_start'], bp['hpf_stop'] - 1)
                spec_s = fft_lp_filter(spec_s, bp['lpf_start'], bp['lpf_stop'])

            sf.write(tmp_wav, spectrogram_to_wave(spec_s, bp['hl'], mp, False).T, bp['sr'])
            ffmprc[d] = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'panic', '-y', '-i', tmp_wav, '-ar', str(mp.param['sr']), '-ac', '2', '-c:a', 'pcm_s16le', tmp_wav2])

    for s in ffmprc:
        ffmprc[s].communicate()
        
    for d in range(bands_n - 1, 0, -1):
        os.remove(f'{tmp_basename}_cmb_spectrogram_to_wave_b{d}_sr' + str(mp.param['band'][d]['sr']) + '.wav')
        tmp_wav2 = f'{tmp_basename}_cmb_spectrogram_to_wave_b{d}_sr' + str(mp.param['sr']) + '.wav'
        wave2, _ = librosa.load(tmp_wav2, mp.param['sr'], False, dtype=np.float32, res_type="sinc_fastest")
        os.remove(tmp_wav2)
        wave = np.add(wave, wave2)

    return wave.T

def fft_lp_filter(spec, bin_start, bin_stop):
    g = 1.0
    for b in range(bin_start, bin_stop):
        g -= 1 / (bin_stop - bin_start)
        spec[:, b, :] = g * spec[:, b, :]
        
    spec[:, bin_stop:, :] *= 0

    return spec


def fft_hp_filter(spec, bin_start, bin_stop):
    g = 1.0
    for b in range(bin_start, bin_stop, -1):
        g -= 1 / (bin_start - bin_stop)
        spec[:, b, :] = g * spec[:, b, :]
    
    spec[:, 0:bin_stop+1, :] *= 0

    return spec

# for ensemble
def get_lp_filter_mask(bins_n, bin_start, bin_stop):
    mask = np.concatenate([
        np.ones((bin_start - 1, 1)),
        np.linspace(1, 0, bin_stop - bin_start + 1)[:, None],
        np.zeros((bins_n - bin_stop, 1))
    ], axis=0)

    return mask
    
    
def get_hp_filter_mask(bins_n, bin_start, bin_stop):
    mask = np.concatenate([
        np.zeros((bin_stop + 1, 1)),
        np.linspace(0, 1, 1 + bin_start - bin_stop)[:, None],
        np.ones((bins_n - bin_start - 2, 1))
    ], axis=0)

    return mask

def mirroring(a, spec_m, input_high_end, mp):
    if 'mirroring' == a:
        mirror = np.flip(np.abs(spec_m[:, mp.param['pre_filter_start']-10-input_high_end.shape[1]:mp.param['pre_filter_start']-10, :]), 1)
        mirror = mirror * np.exp(1.j * np.angle(input_high_end))
        
        return np.where(np.abs(input_high_end) <= np.abs(mirror), input_high_end, mirror)
        
    if 'mirroring2' == a:
        mirror = np.flip(np.abs(spec_m[:, mp.param['pre_filter_start']-10-input_high_end.shape[1]:mp.param['pre_filter_start']-10, :]), 1)
        mi = np.multiply(mirror, input_high_end)
        
        return np.where(np.abs(input_high_end) <= np.abs(mi), input_high_end, mi)
