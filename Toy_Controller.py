import flet as ft
import asyncio
import json
import logging
import websockets
import math # Waveの計算で使用
from buttplug import WebsocketConnector
from buttplug.client import Client, Device

# --- 設定 ---
DEBUG_MODE = False
GAME_WS_URL = "ws://localhost:11451/ws"
INTIFACE_WS_URL = "ws://127.0.0.1:12345"
CONFIG_FILE = "config.json"
LOG_FILE = "output.log"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logging.getLogger().addHandler(file_handler)


# --- グローバル変数 ---
cli = None
is_shutting_down = False
background_tasks = set()

# デバイス属性
managed_devices = {}
signal_assignments = {"piston": None, "vibe": None}

#モード
current_piston_mode = 0
current_vibe_mode = 0

# Piston信号用の設定
piston_pos_min = 0.0
piston_pos_max = 0.8
PISTON_SPEED_MAP = {1: 0.9, 2: 0.5, 3: 0.4}

# Vibe信号用の設定
VIBE_STRENGTH_MAP = {1: 0.5, 2: 1.0}
VIBE_MIN_STRENGTH_MAP = {1: 0.3, 2: 0.6} 

# Vibe信号をPistonデバイスで動かす用の設定
vibe_as_piston_pos_min = 0.0
vibe_as_piston_pos_max = 0.8
VIBE_AS_PISTON_SPEED_MAP = {1: 0.9, 2: 0.4}


# --- 設定ファイル管理 ---
def save_config():
    config_data = {
        "piston_speed": {str(k): v for k, v in PISTON_SPEED_MAP.items()},
        "piston_range": {"min": piston_pos_min, "max": piston_pos_max},
        "vibe_strength": {str(k): v for k, v in VIBE_STRENGTH_MAP.items()},
        "vibe_min_strength": {str(k): v for k, v in VIBE_MIN_STRENGTH_MAP.items()},
        "vibe_as_piston_speed": {str(k): v for k, v in VIBE_AS_PISTON_SPEED_MAP.items()},
        "vibe_as_piston_range": {"min": vibe_as_piston_pos_min, "max": vibe_as_piston_pos_max},
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        logging.info(f"設定を {CONFIG_FILE} に保存しました。")
    except Exception as e:
        logging.error(f"設定ファイルの保存中にエラーが発生しました: {e}")

def load_config():
    global PISTON_SPEED_MAP, VIBE_STRENGTH_MAP, piston_pos_min, piston_pos_max
    global VIBE_AS_PISTON_SPEED_MAP, vibe_as_piston_pos_min, vibe_as_piston_pos_max
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        
        # Piston Speed
        default_piston_speed = {1: 0.9, 2: 0.5, 3: 0.4}
        loaded_piston_speed = {int(k): v for k, v in config.get("piston_speed", {}).items()}
        PISTON_SPEED_MAP = {**default_piston_speed, **loaded_piston_speed}

        # Piston Range
        piston_range = config.get("piston_range", {})
        piston_pos_min = piston_range.get("min", 0.0)
        piston_pos_max = piston_range.get("max", 0.8)

        # Vibe Strength
        default_vibe_strength = {1: 0.5, 2: 1.0}
        loaded_vibe_strength = {int(k): v for k, v in config.get("vibe_strength", {}).items()}
        VIBE_STRENGTH_MAP = {**default_vibe_strength, **loaded_vibe_strength}

        default_vibe_min_strength = {1: 0.1, 2: 0.2}
        loaded_vibe_min_strength = {int(k): v for k, v in config.get("vibe_min_strength", {}).items()}
        VIBE_MIN_STRENGTH_MAP = {**default_vibe_min_strength, **loaded_vibe_min_strength}

        # Vibe as Piston Speed
        default_vaps_speed = {1: 0.9, 2: 0.4}
        loaded_vaps_speed = {int(k): v for k, v in config.get("vibe_as_piston_speed", {}).items()}
        VIBE_AS_PISTON_SPEED_MAP = {**default_vaps_speed, **loaded_vaps_speed}

        # Vibe as Piston Range
        vaps_range = config.get("vibe_as_piston_range", {})
        vibe_as_piston_pos_min = vaps_range.get("min", 0.0)
        vibe_as_piston_pos_max = vaps_range.get("max", 0.8)

        logging.info(f"{CONFIG_FILE} を正常に読み込みました。")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.warning(f"{CONFIG_FILE} が読み込めませんでした ({e})。デフォルト設定で新しいファイルを作成します。")
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

# --- デバイス制御タスク ---
async def piston_worker():
    is_homed = True
    target_position = piston_pos_max
    while not is_shutting_down:
        try:
            device_index = signal_assignments.get("piston")

            # ↓↓↓ ここから追加 ↓↓↓
            # そもそもデバイスが割り当てられていない場合は待機
            if device_index is None:
                await asyncio.sleep(0.1)
                continue

            # デバイス情報を取得
            device_info = managed_devices.get(device_index, {})
            capabilities = device_info.get("capabilities", [])

            # Piston機能がないデバイスが割り当てられた場合は何もしない
            # (VibeデバイスがPiston信号に割り当てられた場合など)
            if 'piston' not in capabilities:
                await asyncio.sleep(0.1)
                continue
            # ↑↑↑ ここまで追加 ↑↑↑

            # デュアルモードの時はpiston_workerは何もしない
            if device_index is not None and device_index == signal_assignments.get("vibe"):
                await asyncio.sleep(0.1)
                continue
                
            actuator = managed_devices.get(device_index, {}).get("device", {}).linear_actuators[0] if device_index is not None else None

            if current_piston_mode > 0 and actuator:
                is_homed = False
                interval = PISTON_SPEED_MAP.get(current_piston_mode, 1.0)
                await actuator.command(position=target_position, duration=int(interval * 1000))
                target_position = piston_pos_min if target_position == piston_pos_max else piston_pos_max
                await asyncio.sleep(interval)
            elif current_piston_mode == 0 and not is_homed and actuator:
                logging.info("ピストンモードがオフのため、ホームポジションに戻ります。")
                await actuator.command(position=0.5, duration=700)
                is_homed = True
                target_position = piston_pos_max
                await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(0.1)
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
                await asyncio.sleep(0.1)
                continue

            device_info = managed_devices.get(vibe_device_index)
            if not device_info:
                await asyncio.sleep(0.1)
                continue
            
            device = device_info["device"]
            capabilities = device_info["capabilities"]
            
            # --- モード判定 ---
            # VibeとPistonが同一デバイスに割り当てられたモード
            is_linked_vibe_mode = (piston_device_index is not None and 
                                   piston_device_index == vibe_device_index and 
                                   'vibe' in capabilities)

            # Vibe機能のみを持つデバイスがVibe信号にのみ割り当てられているモード
            is_vibe_only_mode = 'vibe' in capabilities and not is_linked_vibe_mode
            
            # Piston機能のみを持つデバイスがVibe信号に割り当てられているモード
            is_piston_as_vibe_mode = 'piston' in capabilities and 'vibe' not in capabilities

            # Vibe/Piston連動モード（スムーズな変化）
            if is_linked_vibe_mode:
                is_homed = True # 他のモードの状態をリセット
                vibrator = device.actuators[0]
                
                # パラメータを取得
                piston_interval = PISTON_SPEED_MAP.get(current_piston_mode, 1.0)
                max_strength = VIBE_STRENGTH_MAP.get(current_vibe_mode, 0.0)
                min_strength = VIBE_MIN_STRENGTH_MAP.get(current_vibe_mode, 0.0)
                
                if min_strength > max_strength:
                    min_strength = max_strength

                if current_piston_mode > 0 and current_vibe_mode > 0:
                    TOTAL_STEPS = 10  # 変化のなめらかさ (分割数)
                    step_interval = piston_interval / TOTAL_STEPS
                    
                    # 強度の変化方向を決定
                    start_strength = min_strength if wave_state_is_high else max_strength
                    end_strength = max_strength if wave_state_is_high else min_strength
                    
                    # 強度をスムーズに変化させるループ
                    for i in range(TOTAL_STEPS):
                        progress = i / (TOTAL_STEPS - 1)
                        current_strength = start_strength + (end_strength - start_strength) * progress
                        
                        await vibrator.command(round(current_strength, 2))
                        await asyncio.sleep(step_interval)
                    
                    # 確実に終点強度に設定
                    await vibrator.command(end_strength)
                    
                    # 次のサイクルのために状態を更新
                    wave_state_is_high = not wave_state_is_high

                elif current_piston_mode == 0 and current_vibe_mode > 0:
                    # Vibe Strength（最大値）で一定に振動させる
                    await vibrator.command(max_strength)
                    await asyncio.sleep(0.1)

                # それ以外（Vibeが0）の場合 -> 停止
                else:
                    await vibrator.command(0.0)
                    await asyncio.sleep(0.1)
            
            # Vibe専用モード (単一強度)
            elif is_vibe_only_mode:
                is_homed = True
                vibrator = device.actuators[0]
                target_strength = VIBE_STRENGTH_MAP.get(current_vibe_mode, 0.0)
                await vibrator.command(target_strength)
                await asyncio.sleep(0.1) # 継続的にコマンドを送る

            # Piston as Vibe モード
            elif is_piston_as_vibe_mode:
                # last_sent_strength は使わないのでリセットは不要
                linear = device.linear_actuators[0]
                if current_vibe_mode > 0:
                    if is_homed:
                        target_position = vibe_as_piston_pos_max
                    is_homed = False
                    interval = VIBE_AS_PISTON_SPEED_MAP.get(current_vibe_mode, 1.0)
                    await linear.command(position=target_position, duration=int(interval * 1000))
                    target_position = vibe_as_piston_pos_min if target_position == vibe_as_piston_pos_max else vibe_as_piston_pos_max
                    await asyncio.sleep(interval)
                elif not is_homed:
                    await linear.command(position=0.5, duration=700)
                    is_homed = True
                else:
                    await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(0.1)

        except (asyncio.CancelledError, KeyError, IndexError): 
            break
        except Exception as e: 
            logging.error(f"振動制御中にエラー: {e}")
            await asyncio.sleep(1)

# --- WebSocketリスナー ---
async def game_websocket_listener(page: ft.Page, game_status: ft.Text, piston_mode_text: ft.Text, vibe_mode_text: ft.Text, pulsing_manager: PulsingManager):
    global current_piston_mode, current_vibe_mode
    while not is_shutting_down:
        try:
            game_status.value = "Waiting..."; game_status.color = ft.Colors.YELLOW
            pulsing_manager.add(game_status)
            async with websockets.connect(GAME_WS_URL) as websocket:
                pulsing_manager.remove(game_status)
                logging.info(f"Connected to GameMOD ({GAME_WS_URL})")
                game_status.value = "Connected"; game_status.color = ft.Colors.GREEN
                page.update()
                async for message in websocket:
                    data = json.loads(message)
                    new_piston_mode = data.get("piston", 0); new_vibe_mode = data.get("vibe", 0)
                    if new_piston_mode != current_piston_mode or new_vibe_mode != current_vibe_mode:
                        piston_mode_text.value = f"Piston Mode: {new_piston_mode}"; vibe_mode_text.value = f"Vibe Mode: {new_vibe_mode}"
                        current_piston_mode = new_piston_mode; current_vibe_mode = new_vibe_mode
                        page.update()
        except asyncio.CancelledError: break
        except Exception: logging.warning(f"ゲームMODへの接続に失敗。5秒後に再接続します。"); await asyncio.sleep(5)
    pulsing_manager.remove(game_status)

# --- Intiface/UI管理 ---
async def intiface_manager(page: ft.Page, intiface_status: ft.Text, game_status: ft.Text, piston_selection_group: ft.RadioGroup, vibe_selection_group: ft.RadioGroup, game_listener_args: tuple, pulsing_manager: PulsingManager, ui_elements: dict):
    global cli, managed_devices

    async def rescan_and_update_ui():
        intiface_status.value = "Scanning for devices..."; intiface_status.color = ft.Colors.BLUE
        pulsing_manager.add(intiface_status)
        
        if not DEBUG_MODE:
            await cli.start_scanning(); await asyncio.sleep(3); await cli.stop_scanning()
        else:
            await asyncio.sleep(1)

        pulsing_manager.remove(intiface_status)
        managed_devices.clear()

        if not DEBUG_MODE:
            for device in cli.devices.values():
                if device.removed: continue
                caps = []
                if hasattr(device, 'linear_actuators') and device.linear_actuators: caps.append('piston')
                if hasattr(device, 'actuators') and device.actuators: caps.append('vibe')
                if caps: managed_devices[device.index] = {"device": device, "name": device.name, "capabilities": caps}
        
        if DEBUG_MODE:
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

    if DEBUG_MODE:
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
        try:
            cli = Client("Python Bridge")
            if not is_retrying:
                intiface_status.value = "Connecting..."; intiface_status.color = ft.Colors.YELLOW
                pulsing_manager.add(intiface_status)
            connector = WebsocketConnector(INTIFACE_WS_URL); await cli.connect(connector)
            is_retrying = False; pulsing_manager.remove(intiface_status)
            intiface_status.value = "Connected"; intiface_status.color = ft.Colors.GREEN
            page.update()

            await rescan_and_update_ui()
            piston_task = asyncio.create_task(piston_worker()); vibe_task = asyncio.create_task(vibe_worker())
            game_task = asyncio.create_task(game_websocket_listener(*game_listener_args, pulsing_manager))

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
            new_workers = {piston_task, vibe_task, game_task, scanner_task}
            active_workers.update(new_workers); background_tasks.update(new_workers)
            await asyncio.gather(*new_workers)

        except asyncio.CancelledError: break
        except Exception as e: logging.error(f"Intifaceマネージャーでエラー: {e}")
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
    global piston_pos_min, piston_pos_max
    page.title = "Toy Controller"; page.window.width = 650; page.window.height = 1000
    page.window.maximizable = False; page.window.resizable = False
    page.scroll = ft.ScrollMode.ADAPTIVE; page.padding = 20
    
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
    speed_1_text = ft.Text(f"Mode 1 (Low) Interval: {PISTON_SPEED_MAP[1]:.1f}s")
    speed_1_slider = ft.Slider(min=0.6, max=2.0, value=PISTON_SPEED_MAP[1], width=200, divisions=14)
    speed_2_text = ft.Text(f"Mode 2 (Medium) Interval: {PISTON_SPEED_MAP[2]:.1f}s")
    speed_2_slider = ft.Slider(min=0.3, max=1.0, value=PISTON_SPEED_MAP[2], width=200, divisions=7)
    speed_3_text = ft.Text(f"Mode 3 (High) Interval: {PISTON_SPEED_MAP[3]:.1f}s")
    speed_3_slider = ft.Slider(min=0.1, max=0.5, value=PISTON_SPEED_MAP[3], width=200, divisions=4)
    
    range_title_piston = ft.Text("Piston Range Settings", size=16, weight=ft.FontWeight.BOLD)
    min_pos_text_piston = ft.Text(f"Min Position: {piston_pos_min:.2f}")
    min_pos_slider_piston = ft.Slider(min=0.0, max=1.0, value=piston_pos_min, width=200, divisions=100)
    max_pos_text_piston = ft.Text(f"Max Position: {piston_pos_max:.2f}")
    max_pos_slider_piston = ft.Slider(min=0.0, max=1.0, value=piston_pos_max, width=200, divisions=100)
    
    range_title_vibe = ft.Text("Piston Range Settings", size=16, weight=ft.FontWeight.BOLD)
    min_pos_text_vibe = ft.Text(f"Min Position: {vibe_as_piston_pos_min:.2f}")
    min_pos_slider_vibe = ft.Slider(min=0.0, max=1.0, value=vibe_as_piston_pos_min, width=200, divisions=100)
    max_pos_text_vibe = ft.Text(f"Max Position: {vibe_as_piston_pos_max:.2f}")
    max_pos_slider_vibe = ft.Slider(min=0.0, max=1.0, value=vibe_as_piston_pos_max, width=200, divisions=100)
    
    range_container = ft.Container(
        content=ft.Column([
            ft.Divider(height=5), range_title_vibe,
            min_pos_text_vibe, min_pos_slider_vibe,
            max_pos_text_vibe, max_pos_slider_vibe,
        ]),
        height=0, opacity=0, animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)
    )

    vibe_settings_title = ft.Text("Vibe Strength Settings", size=16, weight=ft.FontWeight.BOLD)
    vibe_1_text = ft.Text(f"Mode 1 (Low) Strength: {VIBE_STRENGTH_MAP[1]:.1f}")
    vibe_1_slider = ft.Slider(min=0.0, max=1.0, value=VIBE_STRENGTH_MAP[1], width=200, divisions=10)
    vibe_2_text = ft.Text(f"Mode 2 (High) Strength: {VIBE_STRENGTH_MAP[2]:.1f}")
    vibe_2_slider = ft.Slider(min=0.0, max=1.0, value=VIBE_STRENGTH_MAP[2], width=200, divisions=10)

    ui_elements = {"vibe_settings_title": vibe_settings_title, "vibe_1_text": vibe_1_text, "vibe_2_text": vibe_2_text}

    vibe_range_title = ft.Text("Vibe Range Settings", size=16, weight=ft.FontWeight.BOLD)
    vibe_min_1_text = ft.Text(f"Mode 1 Min Strength: {VIBE_MIN_STRENGTH_MAP[1]:.1f}")
    vibe_min_1_slider = ft.Slider(min=0.0, max=1.0, value=VIBE_MIN_STRENGTH_MAP[1], width=200, divisions=10)
    vibe_min_2_text = ft.Text(f"Mode 2 Min Strength: {VIBE_MIN_STRENGTH_MAP[2]:.1f}")
    vibe_min_2_slider = ft.Slider(min=0.0, max=1.0, value=VIBE_MIN_STRENGTH_MAP[2], width=200, divisions=10)
    
    vibe_range_container = ft.Container(
        content=ft.Column([
            ft.Divider(height=5),
            vibe_range_title,
            vibe_min_1_text, vibe_min_1_slider,
            vibe_min_2_text, vibe_min_2_slider,
        ]),
        height=0, opacity=0, animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)
    )

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
        
        # Pistonのみのデバイスは排他選択
        is_exclusive = 'piston' in capabilities and 'vibe' not in capabilities
        
        signal_assignments["piston"] = selected_index
        logging.info(f"Piston signal assigned to: {device_info.get('name')}")
        
        if is_exclusive and signal_assignments["vibe"] == selected_index:
            signal_assignments["vibe"] = None
            vibe_selection_group.value = None

            # Vibe設定UIをデフォルトに戻す
            vibe_settings_title.value = "Vibe Strength Settings"

            vibe_1_slider.min = 0.0; vibe_1_slider.max = 1.0; vibe_1_slider.divisions = 10
            vibe_1_slider.value = VIBE_STRENGTH_MAP[1]
            vibe_2_slider.min = 0.0; vibe_2_slider.max = 1.0; vibe_2_slider.divisions = 10
            vibe_2_slider.value = VIBE_STRENGTH_MAP[2]

            vibe_1_text.value = f"Mode 1 (Low) Strength: {VIBE_STRENGTH_MAP[1]:.1f}"
            vibe_2_text.value = f"Mode 2 (High) Strength: {VIBE_STRENGTH_MAP[2]:.1f}"
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

        # Pistonのみのデバイスは排他選択
        if is_piston_only and signal_assignments["piston"] == selected_index:
            signal_assignments["piston"] = None
            piston_selection_group.value = None
        
        if is_piston_only:
            vibe_settings_title.value = "Piston Speed Settings"
            vibe_1_slider.min = 0.1; vibe_1_slider.max = 2.0; vibe_1_slider.divisions = 19
            vibe_1_slider.value = VIBE_AS_PISTON_SPEED_MAP.get(1, 0.9)
            vibe_2_slider.min = 0.1; vibe_2_slider.max = 1.0; vibe_2_slider.divisions = 9
            vibe_2_slider.value = VIBE_AS_PISTON_SPEED_MAP.get(2, 0.4)
            vibe_1_text.value = f"Mode 1 (Low) Interval: {VIBE_AS_PISTON_SPEED_MAP.get(1, 0.9):.1f}s"
            vibe_2_text.value = f"Mode 2 (High) Interval: {VIBE_AS_PISTON_SPEED_MAP.get(2, 0.4):.1f}s"
            range_container.height = 215
            range_container.opacity = 1
        # Vibe機能を持つデバイスの場合
        else: 
            vibe_settings_title.value = "Vibe Strength Settings"
            vibe_1_slider.min = 0.0; vibe_1_slider.max = 1.0; vibe_1_slider.divisions = 10
            vibe_1_slider.value = VIBE_STRENGTH_MAP.get(1, 0.5)
            vibe_2_slider.min = 0.0; vibe_2_slider.max = 1.0; vibe_2_slider.divisions = 10
            vibe_2_slider.value = VIBE_STRENGTH_MAP.get(2, 1.0)
            vibe_1_text.value = f"Mode 1 (Low) Strength: {VIBE_STRENGTH_MAP.get(1, 0.5):.1f}"
            vibe_2_text.value = f"Mode 2 (High) Strength: {VIBE_STRENGTH_MAP.get(2, 1.0):.1f}"
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
        # 最小値が最大値を超えないようにする
        if new_min > VIBE_STRENGTH_MAP[mode]:
            new_min = VIBE_STRENGTH_MAP[mode]
            e.control.value = new_min

        VIBE_MIN_STRENGTH_MAP[mode] = new_min
        if mode == 1:
            vibe_min_1_text.value = f"Mode 1 Min Strength: {new_min:.1f}"
        else:
            vibe_min_2_text.value = f"Mode 2 Min Strength: {new_min:.1f}"
        page.update()    

    piston_selection_group.on_change = on_piston_device_selected
    vibe_selection_group.on_change = on_vibe_device_selected

    def save_on_change_end(e): save_config()

    def on_speed_slider_change(e, mode):
        PISTON_SPEED_MAP[mode] = round(e.control.value, 1)
        speed_1_text.value = f"Mode 1 (Low) Interval: {PISTON_SPEED_MAP[1]:.1f}s"
        speed_2_text.value = f"Mode 2 (Medium) Interval: {PISTON_SPEED_MAP[2]:.1f}s"
        speed_3_text.value = f"Mode 3 (High) Interval: {PISTON_SPEED_MAP[3]:.1f}s"
        page.update()

    def on_min_pos_change_piston(e):
        global piston_pos_min, piston_pos_max
        new_min = round(e.control.value, 2)
        if new_min > piston_pos_max:
            piston_pos_max = new_min
            max_pos_slider_piston.value = new_min
            max_pos_text_piston.value = f"Max Position: {new_min:.2f}"
        piston_pos_min = new_min
        min_pos_text_piston.value = f"Min Position: {new_min:.2f}"
        page.update()

    def on_max_pos_change_piston(e):
        global piston_pos_min, piston_pos_max
        new_max = round(e.control.value, 2)
        if new_max < piston_pos_min:
            piston_pos_min = new_max
            min_pos_slider_piston.value = new_max
            min_pos_text_piston.value = f"Min Position: {new_max:.2f}"
        piston_pos_max = new_max
        max_pos_text_piston.value = f"Max Position: {new_max:.2f}"
        page.update()

    def on_min_pos_change_vibe(e):
        global vibe_as_piston_pos_min, vibe_as_piston_pos_max
        new_min = round(e.control.value, 2)
        if new_min > vibe_as_piston_pos_max:
            vibe_as_piston_pos_max = new_min
            max_pos_slider_vibe.value = new_min
            max_pos_text_vibe.value = f"Max Position: {new_min:.2f}"
        vibe_as_piston_pos_min = new_min
        min_pos_text_vibe.value = f"Min Position: {new_min:.2f}"
        page.update()

    def on_max_pos_change_vibe(e):
        global vibe_as_piston_pos_min, vibe_as_piston_pos_max
        new_max = round(e.control.value, 2)
        if new_max < vibe_as_piston_pos_min:
            vibe_as_piston_pos_min = new_max
            min_pos_slider_vibe.value = new_max
            min_pos_text_vibe.value = f"Min Position: {new_max:.2f}"
        vibe_as_piston_pos_max = new_max
        max_pos_text_vibe.value = f"Max Position: {new_max:.2f}"
        page.update()

    def on_vibe_settings_slider_change(e, mode):
        if "Speed" in vibe_settings_title.value:
            VIBE_AS_PISTON_SPEED_MAP[mode] = round(e.control.value, 1)
            if mode == 1:
                vibe_1_text.value = f"Mode 1 (Low) Interval: {VIBE_AS_PISTON_SPEED_MAP[1]:.1f}s"
            else:
                vibe_2_text.value = f"Mode 2 (High) Interval: {VIBE_AS_PISTON_SPEED_MAP[2]:.1f}s"
        else:
            new_max_strength = round(e.control.value, 2)
            VIBE_STRENGTH_MAP[mode] = new_max_strength
            if mode == 1:
                vibe_1_text.value = f"Mode 1 (Low) Strength: {new_max_strength:.1f}"
            else:
                vibe_2_text.value = f"Mode 2 (High) Strength: {new_max_strength:.1f}"

            # Min Strengthが新しいMax Strengthを超えていないかチェック
            current_min_strength = VIBE_MIN_STRENGTH_MAP.get(mode)
            if current_min_strength > new_max_strength:
                # Min StrengthをMax Strengthと同じ値まで引き下げる
                VIBE_MIN_STRENGTH_MAP[mode] = new_max_strength
                
                # Min StrengthのUI（スライダーとテキスト）も更新する
                if mode == 1:
                    vibe_min_1_slider.value = new_max_strength
                    vibe_min_1_text.value = f"Mode 1 Min Strength: {new_max_strength:.1f}"
                else: # mode == 2
                    vibe_min_2_slider.value = new_max_strength
                    vibe_min_2_text.value = f"Mode 2 Min Strength: {new_max_strength:.1f}"
        page.update()

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
    
    all_sliders = [
        speed_1_slider, speed_2_slider, speed_3_slider,
        min_pos_slider_piston, max_pos_slider_piston,
        min_pos_slider_vibe, max_pos_slider_vibe,
        vibe_1_slider, vibe_2_slider,
        vibe_min_1_slider, vibe_min_2_slider
    ]
    for slider in all_sliders:
        slider.on_change_end = save_on_change_end

    page.add(
        ft.Column(
            controls=[
                ft.Row([ft.Column([intiface_status_label, game_status_label], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.END), ft.Column([intiface_status_value, game_status_value], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.START)], alignment=ft.MainAxisAlignment.CENTER, spacing=10),
                ft.Divider(height=10),
                ft.Card(content=ft.Container(content=ft.Row([ft.Column([piston_selection_title, piston_selection_group], expand=True), ft.VerticalDivider(), ft.Column([vibe_selection_title, vibe_selection_group], expand=True)], spacing=20, vertical_alignment=ft.CrossAxisAlignment.START), padding=20)),
                ft.Row(
                    controls=[
                        ft.Card(
                            expand=True,
                            content=ft.Container(
                                content=ft.Column(
                                    [
                                        ft.ListTile(title=ft.Text("Piston Settings", weight=ft.FontWeight.BOLD, size=18), leading=ft.Icon(ft.Icons.LINEAR_SCALE)),
                                        piston_mode_display, ft.Divider(height=5),
                                        speed_settings_title, speed_1_text, speed_1_slider, speed_2_text, speed_2_slider, speed_3_text, speed_3_slider,
                                        ft.Divider(height=5),
                                        range_title_piston, min_pos_text_piston, min_pos_slider_piston, max_pos_text_piston, max_pos_slider_piston,
                                    ], spacing=5
                                ), padding=15
                            )
                        ),
                        ft.Card(
                            expand=True,
                            content=ft.Container(
                                content=ft.Column(
                                    [
                                        ft.ListTile(title=ft.Text("Vibration Settings", weight=ft.FontWeight.BOLD, size=18), leading=ft.Icon(ft.Icons.VIBRATION)),
                                        vibe_mode_display, ft.Divider(height=5),
                                        vibe_settings_title, vibe_1_text, vibe_1_slider, vibe_2_text, vibe_2_slider,
                                        range_container,
                                        vibe_range_container
                                    ], spacing=5
                                ), padding=15
                            )
                        )
                    ], spacing=20, vertical_alignment=ft.CrossAxisAlignment.START
                )
            ], spacing=20
        )
    )

    async def on_disconnect_handler(e):
        global cli, is_shutting_down
        if is_shutting_down: return
        is_shutting_down = True; logging.info("最終設定を保存します。"); save_config()
        logging.info("切断イベント受信。クリーンアップ処理を開始します。"); pulsing_manager.clear()
        tasks_to_cancel = list(background_tasks)
        for task in tasks_to_cancel:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        if cli and cli.connected:
            try: await cli.disconnect()
            except Exception as ex: logging.error(f"クリーン切断中のエラー: {ex}")
        logging.info("クリーンアップ完了。")

    page.on_disconnect = on_disconnect_handler
    game_args = (page, game_status_value, piston_mode_display, vibe_mode_display)
    manager_args = (page, intiface_status_value, game_status_value, piston_selection_group, vibe_selection_group, game_args, pulsing_manager, ui_elements)
    main_task = asyncio.create_task(intiface_manager(*manager_args))
    background_tasks.add(main_task)
    try: await main_task
    except asyncio.CancelledError: pass

if __name__ == "__main__":
    ft.app(target=main)