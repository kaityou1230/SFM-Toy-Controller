import flet as ft
import asyncio
import json
import logging
import websockets
import math
import time
import config
from buttplug import WebsocketConnector
from buttplug.client import Client, Device

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler(config.LOG_FILE, mode='w', encoding='utf-8')
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logging.getLogger().addHandler(file_handler)

# --- グローバル変数 ---
cli = None
is_shutting_down = False
background_tasks = set()
pose_event_queue = asyncio.Queue()
is_post_climax_cooldown = False
is_wireless_mode = False
is_idle_motion_enabled = False
idle_motion_interval = 4.0
is_slider_dragging = False

page_ref = None
piston_gauge_ref = None
vibe_gauge_ref = None

managed_devices = {}
signal_assignments = {"piston": None, "vibe": None}

current_piston_mode = 0
current_vibe_mode = 0
current_progress = 0.0
current_animation_hash = 0

def save_config():
    config_data = {
        "piston_speed": {str(k): v for k, v in config.PISTON_SPEED_MAP.items()},
        "piston_range": {"min": config.piston_pos_min, "max": config.piston_pos_max},
        "vibe_strength": {str(k): v for k, v in config.VIBE_STRENGTH_MAP.items()},
        "vibe_min_strength": {str(k): v for k, v in config.VIBE_MIN_STRENGTH_MAP.items()},
        "vibe_as_piston_speed": {str(k): v for k, v in config.VIBE_AS_PISTON_SPEED_MAP.items()},
        "vibe_as_piston_range": {"min": config.vibe_as_piston_pos_min, "max": config.vibe_as_piston_pos_max},
        "pose_ranges": {
            str(pose_id): {"min": profile["min_pos"], "max": profile["max_pos"]}
            for pose_id, profile in config.POSE_PROFILES.items()
        }
    }
    try:
        with open(config.CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        logging.error(f"設定ファイルの保存中にエラーが発生しました: {e}")

def load_config():
    try:
        with open(config.CONFIG_FILE, 'r') as f:
            loaded_data = json.load(f)
        
        default_piston_speed = {1: 0.9, 2: 0.5, 3: 0.4}
        loaded_piston_speed = {int(k): v for k, v in loaded_data.get("piston_speed", {}).items()}
        config.PISTON_SPEED_MAP = {**default_piston_speed, **loaded_piston_speed}

        piston_range = loaded_data.get("piston_range", {})
        config.piston_pos_min = piston_range.get("min", 0.0)
        config.piston_pos_max = piston_range.get("max", 0.8)

        default_vibe_strength = {1: 0.5, 2: 1.0}
        loaded_vibe_strength = {int(k): v for k, v in loaded_data.get("vibe_strength", {}).items()}
        config.VIBE_STRENGTH_MAP = {**default_vibe_strength, **loaded_vibe_strength}

        default_vibe_min_strength = {1: 0.1, 2: 0.2}
        loaded_vibe_min_strength = {int(k): v for k, v in loaded_data.get("vibe_min_strength", {}).items()}
        config.VIBE_MIN_STRENGTH_MAP = {**default_vibe_min_strength, **loaded_vibe_min_strength}

        default_vaps_speed = {1: 0.9, 2: 0.4}
        loaded_vaps_speed = {int(k): v for k, v in loaded_data.get("vibe_as_piston_speed", {}).items()}
        config.VIBE_AS_PISTON_SPEED_MAP = {**default_vaps_speed, **loaded_vaps_speed}

        vaps_range = loaded_data.get("vibe_as_piston_range", {})
        config.vibe_as_piston_pos_min = vaps_range.get("min", 0.0)
        config.vibe_as_piston_pos_max = vaps_range.get("max", 0.8)

        loaded_pose_ranges = loaded_data.get("pose_ranges", {})
        for pose_id_str, ranges in loaded_pose_ranges.items():
            pose_id = int(pose_id_str)
            if pose_id in config.POSE_PROFILES:
                config.POSE_PROFILES[pose_id]["min_pos"] = ranges.get("min", config.POSE_PROFILES[pose_id]["min_pos"])
                config.POSE_PROFILES[pose_id]["max_pos"] = ranges.get("max", config.POSE_PROFILES[pose_id]["max_pos"])
        
        logging.info(f"{config.CONFIG_FILE} was loaded successfully.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.warning(f"Failed to load {config.CONFIG_FILE} ({e}). A new file will be created with default settings.")
        save_config()

# --- 点滅マネージャー ---
class PulsingManager:
    def __init__(self, page: ft.Page, interval: float = 0.8):
        self.page = page
        self.interval = interval
        self.controls = set()
        self._task = None
        self._is_pulsed = False

    async def _run(self):
        try:
            while self.controls:
                loop_start_time = asyncio.get_event_loop().time()
                self._is_pulsed = not self._is_pulsed
                opacity = 0.4 if self._is_pulsed else 1.0
                for control in self.controls:
                    control.opacity = opacity
                if self.page.session:
                    try:
                        self.page.update()
                    except Exception as e:
                        logging.warning(f"PulsingManager: page.update() failed, stopping pulse. Error: {e}")
                        break
                elapsed = asyncio.get_event_loop().time() - loop_start_time
                sleep_duration = max(0, self.interval - elapsed)
                await asyncio.sleep(sleep_duration)
        except asyncio.CancelledError:
            pass
        finally:
            for control in self.controls:
                control.opacity = 1.0
            if self.page.session:
                try:
                    self.page.update()
                except Exception:
                    pass
            self._task = None

    def add(self, *controls: ft.Text):
        self.controls.update(controls)
        if not self._task or self._task.done():
            self._is_pulsed = False
            self._task = asyncio.create_task(self._run())
            background_tasks.add(self._task)

    def remove(self, *controls: ft.Text):
        for control in controls:
            if control in self.controls:
                self.controls.remove(control)
                control.opacity = 1.0
        if not self.controls and self._task and not self._task.done():
            self._task.cancel()
        if self.page.session:
            self.page.update()
    
    def clear(self):
        if self._task and not self._task.done():
            self._task.cancel()
        for control in self.controls:
            control.opacity = 1.0
        self.controls.clear()
        if self.page.session:
            self.page.update()

# --- ワーカー ---
async def idle_worker():

    logging.info("Idle worker started.")
    was_idling = False
    target_position = 0.5

    while not is_shutting_down:
        try:
            if is_slider_dragging:
                await asyncio.sleep(0.1)
                continue
            if not is_idle_motion_enabled:
                was_idling = False
                await asyncio.sleep(0.5)
                continue

            is_pose_active = current_animation_hash in config.POSE_PROFILES
            is_climax_active = current_animation_hash in config.CLIMAX_HASHES
            is_piston_mode_active = current_piston_mode > 0
            is_vibe_mode_active = current_vibe_mode > 0

            if is_pose_active or is_climax_active or is_piston_mode_active or is_vibe_mode_active:
                was_idling = False
                await asyncio.sleep(0.5)
                continue

            actuator = None
            piston_device_index = signal_assignments.get("piston")
            if piston_device_index is not None:
                device_info = managed_devices.get(piston_device_index, {})
                if 'piston' in device_info.get("capabilities", []):
                    actuator = device_info.get("device").linear_actuators[0]
            
            if actuator is None:
                vibe_device_index = signal_assignments.get("vibe")
                if vibe_device_index is not None:
                    device_info = managed_devices.get(vibe_device_index, {})
                    if 'piston' in device_info.get("capabilities", []):
                        actuator = device_info.get("device").linear_actuators[0]

            if not actuator:
                was_idling = False
                await asyncio.sleep(0.5)
                continue
            
            if is_wireless_mode:
                duration_ms = int(idle_motion_interval * 1000)
                
                target_position = config.piston_pos_max if target_position == config.piston_pos_min else config.piston_pos_min

                await actuator.command(position=target_position, duration=duration_ms)
                if piston_gauge_ref and page_ref and page_ref.session:
                    piston_gauge_ref.value = target_position; page_ref.update()

                await asyncio.sleep(idle_motion_interval + 0.2)

            else:
                speed = math.pi / idle_motion_interval if idle_motion_interval > 0 else 0
                position = (math.sin(time.monotonic() * speed) + 1) / 2.0
                mapped_position = config.piston_pos_min + (position * (config.piston_pos_max - config.piston_pos_min))

                if not was_idling:
                    logging.info("Idle motion starting smoothly.")
                    await actuator.command(position=mapped_position, duration=500)
                    await asyncio.sleep(0.5)
                    was_idling = True

                if piston_gauge_ref and page_ref and page_ref.session:
                    piston_gauge_ref.value = mapped_position
                    page_ref.update()
                
                await actuator.command(position=mapped_position, duration=150)
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Error in idle_worker: {e}")
            was_idling = False
            await asyncio.sleep(1)

async def piston_worker():
    is_homed = True
    target_position = config.piston_pos_max
    while not is_shutting_down:
        try:
            if (current_animation_hash in config.POSE_PROFILES):
                await asyncio.sleep(0.1)
                continue

            if current_animation_hash in config.CLIMAX_HASHES:
                await asyncio.sleep(0.1)
                continue

            device_index = signal_assignments.get("piston")

            if device_index is None:
                await asyncio.sleep(0.02)
                continue

            device_info = managed_devices.get(device_index, {})
            capabilities = device_info.get("capabilities", [])

            if 'piston' not in capabilities:
                await asyncio.sleep(0.02)
                continue

            if device_index is not None and device_index == signal_assignments.get("vibe"):
                await asyncio.sleep(0.05)
                continue
                
            actuator = managed_devices.get(device_index, {}).get("device", {}).linear_actuators[0] if device_index is not None else None

            if current_piston_mode > 0 and actuator:
                is_homed = False
                interval = config.PISTON_SPEED_MAP.get(current_piston_mode, 1.0)

                if piston_gauge_ref and page_ref and page_ref.session:
                    piston_gauge_ref.value = target_position
                    page_ref.update()

                await actuator.command(position=target_position, duration=int(interval * 1000))
                target_position = config.piston_pos_min if target_position == config.piston_pos_max else config.piston_pos_max
                await asyncio.sleep(interval)
            elif current_piston_mode == 0 and not is_homed and actuator:
                logging.info("Piston mode is off. Returning to home position.")

                if piston_gauge_ref and page_ref and page_ref.session:
                    piston_gauge_ref.value = 0.5
                    page_ref.update()

                await actuator.command(position=0.5, duration=700)
                is_homed = True
                target_position = config.piston_pos_max
                await asyncio.sleep(0.05)

            else:
                await asyncio.sleep(0.05)
        except (asyncio.CancelledError, KeyError, IndexError): break
        except Exception as e: logging.error(f"ピストン制御中にエラー: {e}"); await asyncio.sleep(1)

async def vibe_worker():
    is_homed = True
    target_position = None
    wave_state_is_high = True

    while not is_shutting_down:
        try:

            vibe_device_index = signal_assignments.get("vibe")
            piston_device_index = signal_assignments.get("piston")

            if vibe_device_index is None:
                await asyncio.sleep(0.05)
                continue

            device_info = managed_devices.get(vibe_device_index)
            if not device_info:
                await asyncio.sleep(0.05)
                continue
            
            device = device_info["device"]
            capabilities = device_info["capabilities"]
            
            is_linked_vibe_mode = (piston_device_index is not None and 
                                   piston_device_index == vibe_device_index and 
                                   'vibe' in capabilities)

            is_vibe_only_mode = 'vibe' in capabilities and not is_linked_vibe_mode

            is_piston_as_vibe_mode = 'piston' in capabilities and 'vibe' not in capabilities

            if is_linked_vibe_mode:
                is_homed = True
                vibrator = device.actuators[0]

                piston_interval = config.PISTON_SPEED_MAP.get(current_piston_mode, 1.0)
                max_strength = config.VIBE_STRENGTH_MAP.get(current_vibe_mode, 0.0)
                min_strength = config.VIBE_MIN_STRENGTH_MAP.get(current_vibe_mode, 0.0)
                
                if min_strength > max_strength:
                    min_strength = max_strength

                if current_piston_mode > 0 and current_vibe_mode > 0:
                    TOTAL_STEPS = 10
                    step_interval = piston_interval / TOTAL_STEPS

                    start_strength = min_strength if wave_state_is_high else max_strength
                    end_strength = max_strength if wave_state_is_high else min_strength
                    
                    for i in range(TOTAL_STEPS):
                        progress = i / (TOTAL_STEPS - 1)
                        current_strength = start_strength + (end_strength - start_strength) * progress

                        if vibe_gauge_ref and page_ref and page_ref.session:
                            vibe_gauge_ref.value = round(current_strength, 2)
                            page_ref.update()

                        await vibrator.command(round(current_strength, 2))
                        await asyncio.sleep(step_interval)
                    
                    await vibrator.command(end_strength)

                    wave_state_is_high = not wave_state_is_high

                elif current_piston_mode == 0 and current_vibe_mode > 0:
                    if vibe_gauge_ref and page_ref and page_ref.session:
                        vibe_gauge_ref.value = max_strength
                        page_ref.update()
                    await vibrator.command(max_strength)
                    await asyncio.sleep(0.05)

                else:
                    if vibe_gauge_ref and page_ref and page_ref.session:
                        vibe_gauge_ref.value = 0.0
                        page_ref.update()
                    await vibrator.command(0.0)
                    await asyncio.sleep(0.05)
            
            elif is_vibe_only_mode:
                is_homed = True
                vibrator = device.actuators[0]
                target_strength = config.VIBE_STRENGTH_MAP.get(current_vibe_mode, 0.0)
                if vibe_gauge_ref and page_ref and page_ref.session:
                    vibe_gauge_ref.value = target_strength
                    page_ref.update()
                await vibrator.command(target_strength)
                await asyncio.sleep(0.05)

            elif is_piston_as_vibe_mode:

                if (current_animation_hash in config.POSE_PROFILES):
                    await asyncio.sleep(0.1)
                    continue

                linear = device.linear_actuators[0]
                if current_vibe_mode > 0:
                    if is_homed:
                        target_position = config.vibe_as_piston_pos_max
                    is_homed = False
                    interval = config.VIBE_AS_PISTON_SPEED_MAP.get(current_vibe_mode, 1.0)
                    if piston_gauge_ref and page_ref and page_ref.session:
                        piston_gauge_ref.value = target_position
                        page_ref.update()
                    if vibe_gauge_ref and page_ref and page_ref.session:
                        vibe_gauge_ref.value = 0.0
                        page_ref.update()
                    await linear.command(position=target_position, duration=int(interval * 1000))
                    target_position = config.vibe_as_piston_pos_min if target_position == config.vibe_as_piston_pos_max else config.vibe_as_piston_pos_max
                    await asyncio.sleep(interval)
                elif not is_homed:
                    if piston_gauge_ref and page_ref and page_ref.session:
                        piston_gauge_ref.value = 0.5
                        page_ref.update()
                    await linear.command(position=0.5, duration=700)
                    is_homed = True
                else:
                    await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.05)

        except (asyncio.CancelledError, KeyError, IndexError): 
            break
        except Exception as e: 
            logging.error(f"振動制御中にエラー: {e}")
            await asyncio.sleep(1)

async def climax_worker():
    is_climax_active_last_frame = False
    
    while not is_shutting_down:
        try:
            is_climax_now = current_animation_hash in config.CLIMAX_HASHES
            actuator = None

            piston_device_index = signal_assignments.get("piston")
            if piston_device_index is not None:
                device_info = managed_devices.get(piston_device_index, {})
                if 'piston' in device_info.get("capabilities", []):
                    actuator = device_info.get("device").linear_actuators[0]
            if actuator is None:
                vibe_device_index = signal_assignments.get("vibe")
                if vibe_device_index is not None:
                    device_info = managed_devices.get(vibe_device_index, {})
                    if 'piston' in device_info.get("capabilities", []):
                        actuator = device_info.get("device").linear_actuators[0]

            if is_climax_now and not is_climax_active_last_frame:
                logging.info(f"Climax Animation Detected ({current_animation_hash})! Executing graph pattern.")
                if actuator:
                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.1; page_ref.update()
                    await actuator.command(position=0.1, duration=200)
                    await asyncio.sleep(0.25)
                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.35; page_ref.update()
                    await actuator.command(position=0.35, duration=100)
                    await asyncio.sleep(0.25)
                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.1; page_ref.update()
                    await actuator.command(position=0.1, duration=250)
                    await asyncio.sleep(0.25)
                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.55; page_ref.update()
                    await actuator.command(position=0.55, duration=280)
                    await asyncio.sleep(0.25)
                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.7; page_ref.update()
                    await actuator.command(position=0.7, duration=150)
                    await asyncio.sleep(0.2)
                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.1; page_ref.update()
                    await actuator.command(position=0.1, duration=200)
                    await asyncio.sleep(0.2)

                    if not is_wireless_mode:
                        logging.info("Wired mode: Executing reverberation pattern.")
                        reverberation_pattern = [
                            (0.2, 100),
                            (0.15, 110),
                            (0.1, 120),
                            (0.05, 140)
                        ]

                        for amplitude, duration_ms in reverberation_pattern:
                            if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = amplitude; page_ref.update()
                            await actuator.command(position=amplitude, duration=duration_ms)
                            await asyncio.sleep(duration_ms / 1000.0)
                            if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = 0.0; page_ref.update()
                            await actuator.command(position=0.0, duration=duration_ms)
                            await asyncio.sleep(duration_ms / 1000.0)
                    else:
                        logging.info("Wireless mode: Skipping reverberation pattern.")
            
            elif not is_climax_now and is_climax_active_last_frame and actuator:
                logging.info("Climax ended. Slowly returning to home position.")
                is_post_climax_cooldown = True
                await actuator.command(position=0.5, duration=500)
                if piston_gauge_ref and page_ref and page_ref.session:
                    piston_gauge_ref.value = 0.5
                    page_ref.update()
                await asyncio.sleep(0.5)

                is_post_climax_cooldown = False
                logging.info("Cooldown finished. Resuming normal operations.")

            else:
                await asyncio.sleep(0.2)

            is_climax_active_last_frame = is_climax_now

        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"絶頂制御中にエラー: {e}")
            is_climax_active_last_frame = False
            await asyncio.sleep(0.5)

class PoseWorker:
    def __init__(self):
        self.state = "INACTIVE"
        self.display_pos = 0.5
        self.display_progress = 0.0
        self.transition_smoothing_factor = 0.15
        self.run_smoothing_factor = 0.1
        self.transition_frame_count = 0
        self.last_wireless_pos = -1

    async def run(self):
        TRANSITION_DURATION_FRAMES = 25

        while not is_shutting_down:
            try:
                try:
                    event = pose_event_queue.get_nowait()
                    if event['type'] == 'POSE_CHANGED':
                        new_hash = event['hash']
                        if new_hash in config.POSE_PROFILES and new_hash not in config.CLIMAX_HASHES:
                            if is_wireless_mode:
                                if self.state != "RUNNING":
                                    logging.info(f"State (Wireless) -> RUNNING for hash {new_hash}")
                                    self.state = "RUNNING"
                            else:
                                if self.state != "TRANSITIONING":
                                    logging.info(f"State (Wired) -> TRANSITIONING for hash {new_hash}")
                                    self.state = "TRANSITIONING"
                                    self.transition_frame_count = 0
                        else:
                            if self.state != "INACTIVE":
                                logging.info("State -> INACTIVE")
                                self.state = "INACTIVE"
                except asyncio.QueueEmpty:
                    pass
                
                actuator = None
                piston_device_index = signal_assignments.get("piston")
                if piston_device_index is not None:
                    device_info = managed_devices.get(piston_device_index, {})
                    if 'piston' in device_info.get("capabilities", []):
                        actuator = device_info.get("device").linear_actuators[0]

                if self.state == "TRANSITIONING":
                    if not actuator: 
                        self.state = "INACTIVE"; continue

                    self.transition_frame_count += 1
                    profile = config.POSE_PROFILES[current_animation_hash]
                    pattern_function, pos_min, pos_max = profile['pattern'], profile['min_pos'], profile['max_pos']

                    target_progress = current_progress
                    distance = target_progress - (self.display_progress % 1.0)
                    if distance < -0.5: distance += 1.0
                    elif distance > 0.5: distance -= 1.0
                    self.display_progress += distance * self.transition_smoothing_factor
                    
                    final_progress = self.display_progress % 1.0
                    mapped_position = pos_min + (pattern_function(final_progress) * (pos_max - pos_min))
                    
                    await actuator.command(position=mapped_position, duration=200)
                    self.display_pos = mapped_position

                    if piston_gauge_ref and page_ref and page_ref.session: piston_gauge_ref.value = self.display_pos; page_ref.update()

                    if self.transition_frame_count >= TRANSITION_DURATION_FRAMES:
                        logging.info("Transition finished. State -> RUNNING.")
                        self.state = "RUNNING"

                elif self.state == "RUNNING":
                    if not actuator: 
                        self.state = "INACTIVE"; continue

                    profile = config.POSE_PROFILES[current_animation_hash]
                    pattern_function, pos_min, pos_max = profile['pattern'], profile['min_pos'], profile['max_pos']
                    
                    if is_wireless_mode:
                        target_progress_source = 0.0
                        if profile.get("is_constant_freq", False):
                            cycle_duration = profile["cycle_duration"]
                            target_progress_source = (time.monotonic() / cycle_duration) % 1.0
                        else:
                            target_progress_source = current_progress

                        target_pos = pos_min if target_progress_source <= 0.5 else pos_max

                        if target_pos != self.last_wireless_pos:
                            logging.info(f"Wireless mode: Position changed to {'MIN' if target_pos == pos_min else 'MAX'}")
                            duration_ms = 400
                            await actuator.command(position=target_pos, duration=duration_ms)
                            if piston_gauge_ref and page_ref and page_ref.session:
                                piston_gauge_ref.value = target_pos; page_ref.update()
                            self.last_wireless_pos = target_pos

                    else:
                        final_progress = 0.0

                        if profile.get("is_constant_freq", False):
                            cycle_duration = profile["cycle_duration"]
                            final_progress = (time.monotonic() / cycle_duration) % 1.0
                        else:
                            target_progress = current_progress
                            distance = target_progress - (self.display_progress % 1.0)
                            if distance < -0.5: distance += 1.0
                            elif distance > 0.5: distance -= 1.0
                            self.display_progress += distance * self.run_smoothing_factor
                            final_progress = self.display_progress % 1.0

                        mapped_position = pos_min + (pattern_function(final_progress) * (pos_max - pos_min))
                        await actuator.command(position=mapped_position, duration=50)
                        self.display_pos = mapped_position 
                        if piston_gauge_ref and page_ref and page_ref.session: 
                            piston_gauge_ref.value = mapped_position; page_ref.update()

                elif self.state == "INACTIVE":
                    is_climax_now = current_animation_hash in config.CLIMAX_HASHES
                    if not is_climax_now:
                        target_pos = 0.5
                        if abs(self.display_pos - target_pos) > 0.01:
                            self.display_pos += (target_pos - self.display_pos) * self.transition_smoothing_factor
                            if actuator:
                                await actuator.command(position=self.display_pos, duration=100)
                await asyncio.sleep(0.02)
            except asyncio.CancelledError: break
            except Exception as e:
                logging.error(f"Error in PoseWorker: {e}", exc_info=True)
                self.state = "INACTIVE"
                await asyncio.sleep(1)

# --- WebSocketリスナー ---
async def game_websocket_listener(page: ft.Page, game_status: ft.Text, piston_mode_text: ft.Text, vibe_mode_text: ft.Text, pulsing_manager: PulsingManager):
    global current_piston_mode, current_vibe_mode, current_pose_id, current_progress, current_animation_hash
    last_hash_notified = -1
    while not is_shutting_down:
        try:
            game_status.value = "Waiting..."; game_status.color = ft.Colors.YELLOW
            pulsing_manager.add(game_status)
            async with websockets.connect(config.GAME_WS_URL) as websocket:
                pulsing_manager.remove(game_status)
                logging.info(f"Connected to GameMOD ({config.GAME_WS_URL})")
                game_status.value = "Connected"; game_status.color = ft.Colors.GREEN
                page.update()
                async for message in websocket:
                    data = json.loads(message)

                    current_piston_mode = data.get("piston", 0)
                    current_vibe_mode = data.get("vibe", 0)
                    current_progress = data.get("progress", 0.0)
                    current_animation_hash = data.get("animation_hash", 0)
                    new_hash = data.get("animation_hash", 0)

                    if new_hash != last_hash_notified:
                        event = {'type': 'POSE_CHANGED', 'hash': new_hash}
                        await pose_event_queue.put(event)
                        logging.info(f"EVENT QUEUED: Pose changed to {new_hash}")
                        last_hash_notified = new_hash

                    current_animation_hash = new_hash

                    piston_mode_text.value = f"Piston Mode: {current_piston_mode}"
                    vibe_mode_text.value = f"Vibe Mode: {current_vibe_mode}"
                    page.update()
        except asyncio.CancelledError: break
        except Exception: logging.warning(f"Failed to connect to the game mod. Retrying in 5 seconds."); await asyncio.sleep(1)
    pulsing_manager.remove(game_status)

# --- Intiface/UI管理 ---
async def intiface_manager(page: ft.Page, intiface_status: ft.Text, game_status: ft.Text, piston_selection_group: ft.RadioGroup, vibe_selection_group: ft.RadioGroup, game_listener_args: tuple, pulsing_manager: PulsingManager, ui_elements: dict):
    global cli, managed_devices

    async def rescan_and_update_ui():
        intiface_status.value = "Scanning for devices..."; intiface_status.color = ft.Colors.BLUE
        pulsing_manager.add(intiface_status)
        
        if not config.DEBUG_MODE:
            await cli.start_scanning(); await asyncio.sleep(3); await cli.stop_scanning()
        else:
            await asyncio.sleep(1)

        pulsing_manager.remove(intiface_status)
        managed_devices.clear()

        if not config.DEBUG_MODE:
            for device in cli.devices.values():
                if device.removed: continue
                caps = []
                if hasattr(device, 'linear_actuators') and device.linear_actuators: caps.append('piston')
                if hasattr(device, 'actuators') and device.actuators: caps.append('vibe')
                if caps: managed_devices[device.index] = {"device": device, "name": device.name, "capabilities": caps}
        
        if config.DEBUG_MODE:
            logging.warning("デバッグモード有効: 偽のデバイスを注入します。")
            class MockActuator:
                def __init__(self):
                    self.last_args = object()
                    self.last_kwargs = object()

                async def command(self, *args, **kwargs):
                    if args != self.last_args or kwargs != self.last_kwargs:
                        logging.info(f"MockActuator received command: {args}, {kwargs}")
                        self.last_args = args
                        self.last_kwargs = kwargs
                    pass
                
            class MockDevice:
                def __init__(self, name, index, actuators, linear_actuators):
                    self.name, self.index, self.actuators, self.linear_actuators = name, index, actuators, linear_actuators

            fake_vibe_device = MockDevice("Fake Vibe Device", 99, [MockActuator()], [])
            managed_devices[99] = {"device": fake_vibe_device, "name": fake_vibe_device.name, "capabilities": ["vibe"]}
            fake_piston_device = MockDevice("Fake Piston Device", 98, [], [MockActuator()])
            managed_devices[98] = {"device": fake_piston_device, "name": fake_piston_device.name, "capabilities": ["piston"]}
            #fake_dual_device = MockDevice("Fake Dual Device", 97, [MockActuator()], [MockActuator()])
            #managed_devices[97] = {"device": fake_dual_device, "name": fake_dual_device.name, "capabilities": ["vibe", "piston"]}
        
        piston_radios = []
        for index, info in managed_devices.items():
            if 'piston' in info['capabilities']:
                piston_radios.append(ft.Radio(value=str(index), label=info['name']))
        piston_selection_group.content.controls = piston_radios if piston_radios else [ft.Text("No piston devices found.")]
        
        vibe_radios = []
        for index, info in managed_devices.items():
            vibe_radios.append(ft.Radio(value=str(index), label=info['name']))
        vibe_selection_group.content.controls = vibe_radios if vibe_radios else [ft.Text("No devices found.")]
        
        piston_selection_group.value = None; vibe_selection_group.value = None
        signal_assignments["piston"] = None; signal_assignments["vibe"] = None
        if not managed_devices: intiface_status.value = "No usable devices found"; intiface_status.color = ft.Colors.ORANGE
        else: intiface_status.value = "Device connected"; intiface_status.color = ft.Colors.GREEN
        page.update()

    if config.DEBUG_MODE:
        logging.warning("デバッグモード: Intiface接続をスキップ、Game接続とWorkerは起動します。")
        intiface_status.value = "Debug Mode"; intiface_status.color = ft.Colors.PURPLE
        pulsing_manager.remove(intiface_status)
        
        await rescan_and_update_ui()
        
        piston_task = asyncio.create_task(piston_worker())
        vibe_task = asyncio.create_task(vibe_worker())
        game_task = asyncio.create_task(game_websocket_listener(*game_listener_args, pulsing_manager))

        debug_workers = {piston_task, vibe_task, game_task}
        background_tasks.update(debug_workers)

        await asyncio.gather(*debug_workers)
        return

    is_retrying = False
    while not is_shutting_down:
        active_workers = set()
        piston_task, vibe_task, pose_task, climax_task, game_task, scanner_task = None, None, None, None, None, None
        try:
            cli = Client("Python Bridge")
            if not is_retrying:
                intiface_status.value = "Connecting..."; intiface_status.color = ft.Colors.YELLOW
                pulsing_manager.add(intiface_status)
            connector = WebsocketConnector(config.INTIFACE_WS_URL); await cli.connect(connector)
            is_retrying = False; pulsing_manager.remove(intiface_status)
            intiface_status.value = "Connected"; intiface_status.color = ft.Colors.GREEN
            page.update()

            await rescan_and_update_ui()
            piston_task = asyncio.create_task(piston_worker())
            vibe_task = asyncio.create_task(vibe_worker())
            pose_worker_instance = PoseWorker()
            pose_task = asyncio.create_task(pose_worker_instance.run())
            climax_task = asyncio.create_task(climax_worker())
            game_task = asyncio.create_task(game_websocket_listener(*game_listener_args, pulsing_manager))
            idle_task = asyncio.create_task(idle_worker())

            async def periodic_scanner():
                known_device_ids = set(d.index for d in cli.devices.values() if not d.removed)
                while True:
                    await asyncio.sleep(10)
                    if not cli or not cli.connected: continue
                    await cli.start_scanning(); await asyncio.sleep(2); await cli.stop_scanning()
                    current_device_ids = set(d.index for d in cli.devices.values() if not d.removed)
                    if current_device_ids != known_device_ids:
                        logging.info("デバイスリストが変更されました。UIを更新します。"); await rescan_and_update_ui()
                        known_device_ids = current_device_ids
            
            scanner_task = asyncio.create_task(periodic_scanner())
            new_workers = {piston_task, vibe_task, pose_task, climax_task, game_task, scanner_task, idle_task}
            active_workers.update(new_workers); background_tasks.update(new_workers)
            await asyncio.gather(*new_workers)

        except asyncio.CancelledError: break
        except Exception as e: logging.error(f"Intiface manager : {e}")
        finally:
            for task in active_workers:
                if not task.done(): task.cancel()
            await asyncio.gather(*active_workers, return_exceptions=True)
            background_tasks.difference_update(active_workers)
            if not is_shutting_down:
                is_retrying = True
                intiface_status.value = "Disconnected. Retrying..."; intiface_status.color = ft.Colors.ORANGE
                game_status.value = "Paused (Waiting for Intiface)..."; game_status.color = ft.Colors.YELLOW
                pulsing_manager.add(intiface_status, game_status)
                piston_selection_group.content.controls = [ft.Text("Waiting for Intiface...")]
                vibe_selection_group.content.controls = [ft.Text("Waiting for Intiface...")]
                page.update()
                await asyncio.sleep(3)


async def main(page: ft.Page):
    load_config()
    page.title = "Toy Controller"
    page.window.width = 650; page.window.height = 1000
    page.window.maximizable = False
    page.window.resizable = False
    page.scroll = None 
    page.padding = 20

    global page_ref, piston_gauge_ref, vibe_gauge_ref
    page_ref = page

    piston_gauge = ft.ProgressBar(value=0, width=280, color=ft.Colors.LIGHT_BLUE_ACCENT, bgcolor="#eeeeee")
    vibe_gauge = ft.ProgressBar(value=0, width=280, color=ft.Colors.PINK_ACCENT, bgcolor="#eeeeee")
    piston_gauge_ref = piston_gauge
    vibe_gauge_ref = vibe_gauge
    
    pulsing_manager = PulsingManager(page)
    intiface_status_label = ft.Text("Intiface:", size=16, weight=ft.FontWeight.BOLD)
    intiface_status_value = ft.Text("Initializing...", size=16, 
                                        animate_opacity=ft.Animation(duration=700, curve=ft.AnimationCurve.EASE_IN_OUT))
    game_status_label = ft.Text("Game:", size=16, weight=ft.FontWeight.BOLD)
    game_status_value = ft.Text("Waiting for Intiface...", size=16, 
                                    animate_opacity=ft.Animation(duration=700, curve=ft.AnimationCurve.EASE_IN_OUT))
    piston_selection_title = ft.Text("Piston Signal Target", weight=ft.FontWeight.BOLD)
    piston_selection_group = ft.RadioGroup(content=ft.Column())
    vibe_selection_title = ft.Text("Vibe Signal Target", weight=ft.FontWeight.BOLD)
    vibe_selection_group = ft.RadioGroup(content=ft.Column())
    piston_mode_display = ft.Text("Piston Mode: 0", weight=ft.FontWeight.BOLD, size=16)
    vibe_mode_display = ft.Text("Vibe Mode: 0", weight=ft.FontWeight.BOLD, size=16)
    speed_settings_title = ft.Text("Piston Speed Settings", size=16, weight=ft.FontWeight.BOLD)
    speed_1_text = ft.Text(f"Mode 1 (Low) Interval: {config.PISTON_SPEED_MAP[1]:.1f}s")
    speed_1_slider = ft.Slider(min=0.6, max=2.0, value=config.PISTON_SPEED_MAP[1], width=200, divisions=14)
    speed_2_text = ft.Text(f"Mode 2 (Medium) Interval: {config.PISTON_SPEED_MAP[2]:.1f}s")
    speed_2_slider = ft.Slider(min=0.3, max=1.0, value=config.PISTON_SPEED_MAP[2], width=200, divisions=7)
    speed_3_text = ft.Text(f"Mode 3 (High) Interval: {config.PISTON_SPEED_MAP[3]:.1f}s")
    speed_3_slider = ft.Slider(min=0.1, max=0.5, value=config.PISTON_SPEED_MAP[3], width=200, divisions=4)
    
    range_title_piston = ft.Text("Piston Range Settings", size=16, weight=ft.FontWeight.BOLD)
    min_pos_text_piston = ft.Text(f"Min Position: {config.piston_pos_min:.2f}")
    min_pos_slider_piston = ft.Slider(min=0.0, max=1.0, value=config.piston_pos_min, width=200, divisions=100)
    max_pos_text_piston = ft.Text(f"Max Position: {config.piston_pos_max:.2f}")
    max_pos_slider_piston = ft.Slider(min=0.0, max=1.0, value=config.piston_pos_max, width=200, divisions=100)
    
    range_title_vibe = ft.Text("Piston Range Settings", size=16, weight=ft.FontWeight.BOLD)
    min_pos_text_vibe = ft.Text(f"Min Position: {config.vibe_as_piston_pos_min:.2f}")
    min_pos_slider_vibe = ft.Slider(min=0.0, max=1.0, value=config.vibe_as_piston_pos_min, width=200, divisions=100)
    max_pos_text_vibe = ft.Text(f"Max Position: {config.vibe_as_piston_pos_max:.2f}")
    max_pos_slider_vibe = ft.Slider(min=0.0, max=1.0, value=config.vibe_as_piston_pos_max, width=200, divisions=100)
    
    range_container = ft.Container(
        content=ft.Column([
            ft.Divider(height=5), range_title_vibe,
            min_pos_text_vibe, min_pos_slider_vibe,
            max_pos_text_vibe, max_pos_slider_vibe,
        ]),
        height=0, opacity=0, animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)
    )

    vibe_settings_title = ft.Text("Vibe Strength Settings", size=16, weight=ft.FontWeight.BOLD)
    vibe_1_text = ft.Text(f"Mode 1 (Low) Strength: {config.VIBE_STRENGTH_MAP[1]:.1f}")
    vibe_1_slider = ft.Slider(min=0.0, max=1.0, value=config.VIBE_STRENGTH_MAP[1], width=200, divisions=10)
    vibe_2_text = ft.Text(f"Mode 2 (High) Strength: {config.VIBE_STRENGTH_MAP[2]:.1f}")
    vibe_2_slider = ft.Slider(min=0.0, max=1.0, value=config.VIBE_STRENGTH_MAP[2], width=200, divisions=10)
    ui_elements = {"vibe_settings_title": vibe_settings_title, "vibe_1_text": vibe_1_text, "vibe_2_text": vibe_2_text}
    vibe_range_title = ft.Text("Vibe Range Settings", size=16, weight=ft.FontWeight.BOLD)
    vibe_min_1_text = ft.Text(f"Mode 1 Min Strength: {config.VIBE_MIN_STRENGTH_MAP[1]:.1f}")
    vibe_min_1_slider = ft.Slider(min=0.0, max=1.0, value=config.VIBE_MIN_STRENGTH_MAP[1], width=200, divisions=10)
    vibe_min_2_text = ft.Text(f"Mode 2 Min Strength: {config.VIBE_MIN_STRENGTH_MAP[2]:.1f}")
    vibe_min_2_slider = ft.Slider(min=0.0, max=1.0, value=config.VIBE_MIN_STRENGTH_MAP[2], width=200, divisions=10)
    
    vibe_range_container = ft.Container(
        content=ft.Column([
            ft.Divider(height=5),
            vibe_range_title,
            vibe_min_1_text, vibe_min_1_slider,
            vibe_min_2_text, vibe_min_2_slider,
        ]),
        height=0, opacity=0, animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)
    )

    pose_settings_title = ft.Text("Motion Range Settings", size=16, weight=ft.FontWeight.BOLD)
    pose_selector = ft.Dropdown(
        label="Motion",
        options=[
            ft.dropdown.Option(key=str(pose_id), text=profile["name"])
            for pose_id, profile in config.POSE_PROFILES.items()
        ],
    )
    pose_min_pos_text = ft.Text("Min Position: --")
    pose_min_pos_slider = ft.Slider(min=0.0, max=1.0, divisions=100, disabled=True)
    pose_max_pos_text = ft.Text("Max Position: --")
    pose_max_pos_slider = ft.Slider(min=0.0, max=1.0, divisions=100, disabled=True)

    def on_idle_switch_change(e):
        global is_idle_motion_enabled
        is_idle_motion_enabled = e.control.value
        logging.info(f"Idle motion toggled: {is_idle_motion_enabled}")
        page.update()

    idle_switch = ft.Switch(label="Filler Interval", value=is_idle_motion_enabled, on_change=on_idle_switch_change)

    idle_interval_text = ft.Text(f": {idle_motion_interval:.1f}s")

    def on_slider_change_display_only(e):
        new_interval = round(e.control.value, 1)
        idle_interval_text.value = f": {new_interval:.1f}s"
        page.update()

    def on_slider_drag_start(e):
        global is_slider_dragging
        is_slider_dragging = True
        logging.info("Slider drag started, pausing motion.")

    def on_slider_drag_end(e):
        global is_slider_dragging, idle_motion_interval
        
        new_interval = round(e.control.value, 1)
        idle_motion_interval = new_interval
        
        is_slider_dragging = False
        logging.info(f"Slider drag ended, resuming motion with interval: {new_interval}s")

    idle_interval_slider = ft.Slider(
        min=1.0,
        max=6.0,
        divisions=5.5,
        value=idle_motion_interval,
        width=200,
        on_change=on_slider_change_display_only,
        on_change_start=on_slider_drag_start,
        on_change_end=on_slider_drag_end
    )

    def save_on_change_end(e):
        save_config()

    def on_pose_selected(e):
        selected_id = int(e.control.value)
        profile = config.POSE_PROFILES[selected_id]
        
        min_val = profile.get("min_pos", 0.0)
        max_val = profile.get("max_pos", 1.0)
        
        pose_min_pos_text.value = f"Min Position: {min_val:.2f}"
        pose_min_pos_slider.value = min_val
        pose_min_pos_slider.disabled = False
        
        pose_max_pos_text.value = f"Max Position: {max_val:.2f}"
        pose_max_pos_slider.value = max_val
        pose_max_pos_slider.disabled = False
        
        page.update()

    def on_pose_slider_change(e, slider_type):
        if not pose_selector.value: return
        
        selected_id = int(pose_selector.value)
        new_value = round(e.control.value, 2)
        
        if slider_type == "min":
            current_max = config.POSE_PROFILES[selected_id]["max_pos"]
            if new_value > current_max:
                new_value = current_max
                e.control.value = new_value
            config.POSE_PROFILES[selected_id]["min_pos"] = new_value
            pose_min_pos_text.value = f"Min Position: {new_value:.2f}"
        else:
            current_min = config.POSE_PROFILES[selected_id]["min_pos"]
            if new_value < current_min:
                new_value = current_min
                e.control.value = new_value
            config.POSE_PROFILES[selected_id]["max_pos"] = new_value
            pose_max_pos_text.value = f"Max Position: {new_value:.2f}"
        
        page.update()

    def _check_and_update_vibe_range_ui():
        
        #logging.info(f"UI Vibility Check: Piston={signal_assignments.get('piston')}, Vibe={signal_assignments.get('vibe')}")
        
        piston_idx = signal_assignments.get("piston")
        vibe_idx = signal_assignments.get("vibe")

        show_vibe_range = False

        if vibe_idx is not None and piston_idx is not None and piston_idx == vibe_idx:
            vibe_device_info = managed_devices.get(vibe_idx, {})
            vibe_capabilities = vibe_device_info.get("capabilities", [])
            if 'vibe' in vibe_capabilities:
                show_vibe_range = True

        if show_vibe_range:
            vibe_range_container.height = 215
            vibe_range_container.opacity = 1
        else:
            vibe_range_container.height = 0
            vibe_range_container.opacity = 0

    def on_piston_device_selected(e):
        selected_index = int(e.control.value)
        device_info = managed_devices.get(selected_index, {})
        capabilities = device_info.get("capabilities", [])
        
        is_exclusive = 'piston' in capabilities and 'vibe' not in capabilities
        
        signal_assignments["piston"] = selected_index
        logging.info(f"Piston signal assigned to: {device_info.get('name')}")
        
        if is_exclusive and signal_assignments["vibe"] == selected_index:
            signal_assignments["vibe"] = None
            vibe_selection_group.value = None

            vibe_settings_title.value = "Vibe Strength Settings"

            vibe_1_slider.min = 0.0; vibe_1_slider.max = 1.0; vibe_1_slider.divisions = 10
            vibe_1_slider.value = config.VIBE_STRENGTH_MAP[1]
            vibe_2_slider.min = 0.0; vibe_2_slider.max = 1.0; vibe_2_slider.divisions = 10
            vibe_2_slider.value = config.VIBE_STRENGTH_MAP[2]

            vibe_1_text.value = f"Mode 1 (Low) Strength: {config.VIBE_STRENGTH_MAP[1]:.1f}"
            vibe_2_text.value = f"Mode 2 (High) Strength: {config.VIBE_STRENGTH_MAP[2]:.1f}"
            range_container.height = 0; range_container.opacity = 0
        
        _check_and_update_vibe_range_ui()
        page.update()

    def on_vibe_device_selected(e):
        selected_index = int(e.control.value)
        device_info = managed_devices.get(selected_index, {})
        capabilities = device_info.get("capabilities", [])
        
        is_piston_only = 'piston' in capabilities and 'vibe' not in capabilities
        
        signal_assignments["vibe"] = selected_index
        logging.info(f"Vibe signal assigned to: {device_info.get('name')}")

        if is_piston_only and signal_assignments["piston"] == selected_index:
            signal_assignments["piston"] = None
            piston_selection_group.value = None
        if is_piston_only:
            vibe_settings_title.value = "Piston Speed Settings"
            vibe_1_slider.min = 0.1; vibe_1_slider.max = 2.0; vibe_1_slider.divisions = 19
            vibe_1_slider.value = config.VIBE_AS_PISTON_SPEED_MAP.get(1, 0.9)
            vibe_2_slider.min = 0.1; vibe_2_slider.max = 1.0; vibe_2_slider.divisions = 9
            vibe_2_slider.value = config.VIBE_AS_PISTON_SPEED_MAP.get(2, 0.4)
            vibe_1_text.value = f"Mode 1 (Low) Interval: {config.VIBE_AS_PISTON_SPEED_MAP.get(1, 0.9):.1f}s"
            vibe_2_text.value = f"Mode 2 (High) Interval: {config.VIBE_AS_PISTON_SPEED_MAP.get(2, 0.4):.1f}s"
            range_container.height = 215
            range_container.opacity = 1
        else: 
            vibe_settings_title.value = "Vibe Strength Settings"
            vibe_1_slider.min = 0.0; vibe_1_slider.max = 1.0; vibe_1_slider.divisions = 10
            vibe_1_slider.value = config.VIBE_STRENGTH_MAP.get(1, 0.5)
            vibe_2_slider.min = 0.0; vibe_2_slider.max = 1.0; vibe_2_slider.divisions = 10
            vibe_2_slider.value = config.VIBE_STRENGTH_MAP.get(2, 1.0)
            vibe_1_text.value = f"Mode 1 (Low) Strength: {config.VIBE_STRENGTH_MAP.get(1, 0.5):.1f}"
            vibe_2_text.value = f"Mode 2 (High) Strength: {config.VIBE_STRENGTH_MAP.get(2, 1.0):.1f}"
            range_container.height = 0
            range_container.opacity = 0

        new_piston_radios = []
        current_piston_selection_value = piston_selection_group.value

        for index, info in managed_devices.items():
            if 'piston' in info['capabilities'] or index == selected_index:
                new_piston_radios.append(ft.Radio(value=str(index), label=info['name']))

        piston_selection_group.content.controls = new_piston_radios

        valid_piston_values = [radio.value for radio in new_piston_radios]
        if current_piston_selection_value in valid_piston_values:
            piston_selection_group.value = current_piston_selection_value
        else:
            piston_selection_group.value = None
            signal_assignments["piston"] = None

        _check_and_update_vibe_range_ui()
        page.update()

    def on_vibe_min_strength_slider_change(e, mode):
        new_min = round(e.control.value, 2)
        if new_min > config.VIBE_STRENGTH_MAP[mode]:
            new_min = config.VIBE_STRENGTH_MAP[mode]
            e.control.value = new_min

        config.VIBE_MIN_STRENGTH_MAP[mode] = new_min
        if mode == 1:
            vibe_min_1_text.value = f"Mode 1 Min Strength: {new_min:.1f}"
        else:
            vibe_min_2_text.value = f"Mode 2 Min Strength: {new_min:.1f}"
        page.update()    

    piston_selection_group.on_change = on_piston_device_selected
    vibe_selection_group.on_change = on_vibe_device_selected

    def save_on_change_end(e): save_config()

    def on_speed_slider_change(e, mode):
        config.PISTON_SPEED_MAP[mode] = round(e.control.value, 1)
        speed_1_text.value = f"Mode 1 (Low) Interval: {config.PISTON_SPEED_MAP[1]:.1f}s"
        speed_2_text.value = f"Mode 2 (Medium) Interval: {config.PISTON_SPEED_MAP[2]:.1f}s"
        speed_3_text.value = f"Mode 3 (High) Interval: {config.PISTON_SPEED_MAP[3]:.1f}s"
        page.update()

    def on_min_pos_change_piston(e):
        new_min = round(e.control.value, 2)
        if new_min > config.piston_pos_max:
            config.piston_pos_max = new_min
            max_pos_slider_piston.value = new_min
            max_pos_text_piston.value = f"Max Position: {new_min:.2f}"
        config.piston_pos_min = new_min
        min_pos_text_piston.value = f"Min Position: {new_min:.2f}"
        page.update()

    def on_max_pos_change_piston(e):
        new_max = round(e.control.value, 2)
        if new_max < config.piston_pos_min:
            config.piston_pos_min = new_max
            min_pos_slider_piston.value = new_max
            min_pos_text_piston.value = f"Min Position: {new_max:.2f}"
        config.piston_pos_max = new_max
        max_pos_text_piston.value = f"Max Position: {new_max:.2f}"
        page.update()

    def on_min_pos_change_vibe(e):
        new_min = round(e.control.value, 2)
        if new_min > config.vibe_as_piston_pos_max:
            config.vibe_as_piston_pos_max = new_min
            max_pos_slider_vibe.value = new_min
            max_pos_text_vibe.value = f"Max Position: {new_min:.2f}"
        config.vibe_as_piston_pos_min = new_min
        min_pos_text_vibe.value = f"Min Position: {new_min:.2f}"
        page.update()

    def on_max_pos_change_vibe(e):
        new_max = round(e.control.value, 2)
        if new_max < config.vibe_as_piston_pos_min:
            config.vibe_as_piston_pos_min = new_max
            min_pos_slider_vibe.value = new_max
            min_pos_text_vibe.value = f"Min Position: {new_max:.2f}"
        config.vibe_as_piston_pos_max = new_max
        max_pos_text_vibe.value = f"Max Position: {new_max:.2f}"
        page.update()

    def on_vibe_settings_slider_change(e, mode):
        if "Speed" in vibe_settings_title.value:
            config.VIBE_AS_PISTON_SPEED_MAP[mode] = round(e.control.value, 1)
            if mode == 1:
                vibe_1_text.value = f"Mode 1 (Low) Interval: {config.VIBE_AS_PISTON_SPEED_MAP[1]:.1f}s"
            else:
                vibe_2_text.value = f"Mode 2 (High) Interval: {config.VIBE_AS_PISTON_SPEED_MAP[2]:.1f}s"
        else:
            new_max_strength = round(e.control.value, 2)
            config.VIBE_STRENGTH_MAP[mode] = new_max_strength
            if mode == 1:
                vibe_1_text.value = f"Mode 1 (Low) Strength: {new_max_strength:.1f}"
            else:
                vibe_2_text.value = f"Mode 2 (High) Strength: {new_max_strength:.1f}"

            current_min_strength = config.VIBE_MIN_STRENGTH_MAP.get(mode)
            if current_min_strength > new_max_strength:
                config.VIBE_MIN_STRENGTH_MAP[mode] = new_max_strength

                if mode == 1:
                    vibe_min_1_slider.value = new_max_strength
                    vibe_min_1_text.value = f"Mode 1 Min Strength: {new_max_strength:.1f}"
                else:
                    vibe_min_2_slider.value = new_max_strength
                    vibe_min_2_text.value = f"Mode 2 Min Strength: {new_max_strength:.1f}"
        page.update()

    def on_wireless_mode_change(e):
        global is_wireless_mode
        is_wireless_mode = e.control.value
        logging.info(f"Wireless device mode toggled: {is_wireless_mode}")
        page.update()

    wireless_switch = ft.Switch(
        label="Bluetooth Device",
        value=is_wireless_mode,
        on_change=on_wireless_mode_change
    )

    speed_1_slider.on_change = lambda e: on_speed_slider_change(e, 1)
    speed_2_slider.on_change = lambda e: on_speed_slider_change(e, 2)
    speed_3_slider.on_change = lambda e: on_speed_slider_change(e, 3)
    min_pos_slider_piston.on_change = on_min_pos_change_piston
    max_pos_slider_piston.on_change = on_max_pos_change_piston
    min_pos_slider_vibe.on_change = on_min_pos_change_vibe
    max_pos_slider_vibe.on_change = on_max_pos_change_vibe
    vibe_1_slider.on_change = lambda e: on_vibe_settings_slider_change(e, 1)
    vibe_2_slider.on_change = lambda e: on_vibe_settings_slider_change(e, 2)
    vibe_min_1_slider.on_change = lambda e: on_vibe_min_strength_slider_change(e, 1)
    vibe_min_2_slider.on_change = lambda e: on_vibe_min_strength_slider_change(e, 2)
    pose_selector.on_change = on_pose_selected
    pose_min_pos_slider.on_change = lambda e: on_pose_slider_change(e, "min")
    pose_max_pos_slider.on_change = lambda e: on_pose_slider_change(e, "max")
    
    all_sliders = [
        speed_1_slider, speed_2_slider, speed_3_slider,
        min_pos_slider_piston, max_pos_slider_piston,
        min_pos_slider_vibe, max_pos_slider_vibe,
        vibe_1_slider, vibe_2_slider,
        vibe_min_1_slider, vibe_min_2_slider,
        pose_min_pos_slider, pose_max_pos_slider 
    ]
    for slider in all_sliders:
        slider.on_change_end = save_on_change_end

    page.add(
        ft.Column(
            expand=True,
            controls=[
                ft.Row(
                    [
                        ft.Column([intiface_status_label, game_status_label], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.END), 
                        ft.Column([intiface_status_value, game_status_value], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.START)
                    ], 
                    alignment=ft.MainAxisAlignment.CENTER, 
                    spacing=10
                ),
                ft.Divider(height=10),
                ft.Card(
                    content=ft.Container(
                        content=ft.Row(
                            [
                                ft.Column([piston_selection_title, piston_selection_group], expand=True), 
                                ft.VerticalDivider(), 
                                ft.Column([vibe_selection_title, vibe_selection_group], expand=True)
                            ], 
                            spacing=20, 
                            vertical_alignment=ft.CrossAxisAlignment.START
                        ), 
                        padding=20
                    )
                ),
                ft.Card(
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row(
                                controls=[
                                    idle_switch,
                                    ft.Row(
                                        controls=[
                                            idle_interval_text,
                                            idle_interval_slider
                                        ],
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    )
                                ],
                                alignment=ft.MainAxisAlignment.START,
                                spacing=0 
                            ),
                            wireless_switch
                        ]),
                        padding=ft.padding.only(left=15, top=10, right=15, bottom=10)
                    )
                ),

                ft.Column(
                    expand=True,
                    scroll=ft.ScrollMode.ADAPTIVE,
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Card(
                                    expand=True,
                                    content=ft.Container(
                                        content=ft.Column(
                                            [
                                                ft.ListTile(title=ft.Text("Piston Settings", weight=ft.FontWeight.BOLD, size=18), leading=ft.Icon(ft.Icons.LINEAR_SCALE),subtitle=piston_gauge),
                                                piston_mode_display, ft.Divider(height=5),
                                                speed_settings_title, speed_1_text, speed_1_slider, speed_2_text, speed_2_slider, speed_3_text, speed_3_slider,
                                                ft.Divider(height=5),
                                                range_title_piston, min_pos_text_piston, min_pos_slider_piston, max_pos_text_piston, max_pos_slider_piston,
                                                ft.Divider(height=15, color=ft.Colors.TRANSPARENT),
                                                pose_settings_title,
                                                pose_selector,
                                                pose_min_pos_text,
                                                pose_min_pos_slider,
                                                pose_max_pos_text,
                                                pose_max_pos_slider,
                                            ], spacing=5
                                        ), padding=15
                                    )
                                ),
                                ft.Card(
                                    expand=True,
                                    content=ft.Container(
                                        content=ft.Column(
                                            [
                                                ft.ListTile(title=ft.Text("Vibration Settings", weight=ft.FontWeight.BOLD, size=18), leading=ft.Icon(ft.Icons.VIBRATION),subtitle=vibe_gauge),
                                                vibe_mode_display, ft.Divider(height=5),
                                                vibe_settings_title, vibe_1_text, vibe_1_slider, vibe_2_text, vibe_2_slider,
                                                range_container,
                                                vibe_range_container
                                            ], spacing=5
                                        ), padding=15
                                    )
                                )
                            ], 
                            spacing=20, 
                            vertical_alignment=ft.CrossAxisAlignment.START
                        )
                    ]
                )
            ],
            spacing=10
        )
    )

    async def on_disconnect_handler(e):
        global cli, is_shutting_down
        if is_shutting_down: return
        is_shutting_down = True; logging.info("Saving final configuration."); save_config()
        logging.info("Disconnect event received. Starting cleanup process."); pulsing_manager.clear()
        tasks_to_cancel = list(background_tasks)
        for task in tasks_to_cancel:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        if cli and cli.connected:
            try: await cli.disconnect()
            except Exception as ex: logging.error(f"クリーン切断中のエラー: {ex}")
        logging.info("Cleanup complete.")

    page.on_disconnect = on_disconnect_handler
    game_args = (page, game_status_value, piston_mode_display, vibe_mode_display)
    manager_args = (page, intiface_status_value, game_status_value, piston_selection_group, vibe_selection_group, game_args, pulsing_manager, ui_elements)
    main_task = asyncio.create_task(intiface_manager(*manager_args))
    background_tasks.add(main_task)
    try: await main_task
    except asyncio.CancelledError: pass

if __name__ == "__main__":
    ft.app(target=main)