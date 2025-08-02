import math
import time

# --- 設定 ---
DEBUG_MODE = False
GAME_WS_URL = "ws://localhost:11451/ws"
INTIFACE_WS_URL = "ws://127.0.0.1:12345"
CONFIG_FILE = "config.json"
LOG_FILE = "output.log"

# --- 設定値 ---
piston_pos_min = 0.0
piston_pos_max = 0.8
PISTON_SPEED_MAP = {1: 0.9, 2: 0.5, 3: 0.4}

vibe_as_piston_pos_min = 0.0
vibe_as_piston_pos_max = 0.8
VIBE_STRENGTH_MAP = {1: 0.5, 2: 1.0}
VIBE_MIN_STRENGTH_MAP = {1: 0.3, 2: 0.6} 
VIBE_AS_PISTON_SPEED_MAP = {1: 0.9, 2: 0.4}

# --- パターン ---
def pattern_1(progress):
    if progress < 0.5:
        sub_progress = progress / 0.5
        return 1.0 - sub_progress
    elif progress < 0.75:
        sub_progress = (progress - 0.5) / 0.25
        return sub_progress * 0.3
    else:
        sub_progress = (progress - 0.75) / 0.25
        return 0.3 + (sub_progress * 0.7)
    
def pattern_1_inverted(progress):
    return 1.0 - pattern_1(progress)

def pattern_2(progress):
    return (math.sin(progress * 2 * math.pi - (math.pi / 2)) + 1) / 2
    
def pattern_3(progress):
    if progress < 0.8:
        return progress / 0.8
    else:
        return 1.0 - ((progress - 0.8) / 0.2)
    
def pattern_4(progress):
    if progress < 0.7:
        return progress / 0.7
    else:
        return 1.0 - ((progress - 0.7) / 0.3)

def pattern_4_inverted(progress):
    return 1.0 - pattern_4(progress)
    
def pattern_5(progress):
    if progress < 0.6:
        return progress / 0.6
    else:
        return 1.0 - ((progress - 0.6) / 0.4)

def pattern_6(progress):

    def ease_in_quad(t):
        return t * t

    def ease_out_quad(t):
        return 1.0 - (1.0 - t) * (1.0 - t)

    if progress < 0.4:
        sub_progress = progress / 0.4
        eased_value = ease_in_quad(sub_progress)
        return 1.0 - eased_value
    else:
        sub_progress = (progress - 0.4) / (1.0 - 0.4)
        eased_value = ease_out_quad(sub_progress)
        return eased_value

def pattern_constant_freq(progress):
    cycle_duration = 0.65
    internal_progress = (time.monotonic() / cycle_duration) % 1.0
    return pattern_5(internal_progress)


# --- ハッシュ値 ---
POSE_PROFILES = {
    1201047697: {"name": "Nipple Play", "min_pos": 0.65, "max_pos": 0.9, "pattern": pattern_6, "is_constant_freq": True, "cycle_duration": 0.76},##OK
    1832166380: {"name": "Clit Play", "min_pos": 0.6, "max_pos": 0.8, "pattern": pattern_4, "is_constant_freq": True, "cycle_duration": 0.65},##OK
    7717404:    {"name": "Stroking", "min_pos": 0.2, "max_pos": 0.6, "pattern": pattern_2, "is_constant_freq": True, "cycle_duration": 0.396},##OK
    505962836:  {"name": "Masturbate", "min_pos": 0.3, "max_pos": 0.8, "pattern": pattern_1_inverted},##OK
    2011001274: {"name": "Three-Leg", "min_pos": 0.0, "max_pos": 0.5, "pattern": pattern_3},##OK
    344055696:  {"name": "Doggy", "min_pos": 0.0, "max_pos": 0.4, "pattern": pattern_1_inverted},##OK
    1945541277: {"name": "Supine", "min_pos": 0.0, "max_pos": 0.5, "pattern": pattern_1_inverted},##OK

    1272021522: {"name": "Standing Doggy", "min_pos": 0.0, "max_pos": 0.7, "pattern": pattern_5},##OK
    126556443:  {"name": "Cowgirl", "min_pos": 0.0, "max_pos": 0.5, "pattern": pattern_4},##OK

    81106989:   {"name": "Dildo (Chair)", "min_pos": 0.2, "max_pos": 0.6, "pattern": pattern_4_inverted},##OK
    1127557836: {"name": "Dildo (Floor, Vaginal)", "min_pos": 0.4, "max_pos": 0.8, "pattern": pattern_4_inverted},##OK
    1067368937: {"name": "Dildo (Floor, Anal)", "min_pos": 0.4, "max_pos": 0.8, "pattern": pattern_4_inverted},##OK
    37429125:   {"name": "Dildo (Standing, Vaginal)", "min_pos": 0.1, "max_pos": 0.6, "pattern": pattern_1_inverted},##OK
    652955773:  {"name": "Dildo (Standing, Anal)", "min_pos": 0.1, "max_pos": 0.6, "pattern": pattern_1_inverted},
    709841502:  {"name": "Dildo (Standing, Oral)", "min_pos": 0.3, "max_pos": 0.7, "pattern": pattern_2},##OK
}

CLIMAX_HASHES = {
    1514068739, # 通常(絶頂)
    551798253,  # 三足(絶頂)
    1352943776, # 四つん這い(絶頂)
    215434987,  # 仰向き(絶頂)
    76164332,   # オナニー(絶頂)
    231319108,  # 乳首(絶頂)
    2060359382, # クリいじり(絶頂)
    48554725,   # しこしこ(射精)
    1584403080, # 騎乗位(射精)
    248983229,  # 立ちバック(射精)
    2029961234, # ディルド(絶頂)
}