import flet as ft
import asyncio
import json
import logging
import websockets
from buttplug import WebsocketConnector
from buttplug.client import Client, Device

# --- 設定 ---
GAME_WS_URL = "ws://localhost:11451/ws"
INTIFACE_WS_URL = "ws://127.0.0.1:12345"
CONFIG_FILE = "config.json"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- グローバル変数 (デフォルト値) ---
vibrator_actuator = None
linear_actuator = None
current_piston_mode = 0
current_vibe_mode = 0
background_tasks = set()
linear_devices = {}
vibrator_devices = {}
cli = None
is_shutting_down = False

piston_pos_min = 0.0
piston_pos_max = 0.8

PISTON_SPEED_MAP = {
    1: 0.9,
    2: 0.5,
    3: 0.4,
}

VIBE_STRENGTH_MAP = {
    1: 0.5,
    2: 1.0,
}

# --- 設定ファイル管理 ---
def save_config():
    # JSONのキーは文字列である必要があるため、intキーをstrに変換
    piston_speed_to_save = {str(k): v for k, v in PISTON_SPEED_MAP.items()}
    vibe_strength_to_save = {str(k): v for k, v in VIBE_STRENGTH_MAP.items()}

    config_data = {
        "piston_speed": piston_speed_to_save,
        "piston_range": {
            "min": piston_pos_min,
            "max": piston_pos_max,
        },
        "vibe_strength": vibe_strength_to_save,
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        logging.info(f"設定を {CONFIG_FILE} に保存しました。")
    except Exception as e:
        logging.error(f"設定ファイルの保存中にエラーが発生しました: {e}")

def load_config():
    global PISTON_SPEED_MAP, VIBE_STRENGTH_MAP, piston_pos_min, piston_pos_max
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        
        # --- Piston Speed の読み込み ---
        # デフォルト設定をベースに、ファイルの内容で上書きする
        default_piston_speed = {1: 0.9, 2: 0.5, 3: 0.4}
        loaded_piston_speed_str_keys = config.get("piston_speed", {})
        loaded_piston_speed = {int(k): v for k, v in loaded_piston_speed_str_keys.items()}
        PISTON_SPEED_MAP = {**default_piston_speed, **loaded_piston_speed}

        # --- Vibe Strength の読み込み ---
        default_vibe_strength = {1: 0.5, 2: 1.0}
        loaded_vibe_strength_str_keys = config.get("vibe_strength", {})
        loaded_vibe_strength = {int(k): v for k, v in loaded_vibe_strength_str_keys.items()}
        VIBE_STRENGTH_MAP = {**default_vibe_strength, **loaded_vibe_strength}

        # --- Piston Range の読み込み ---
        piston_range = config.get("piston_range", {})
        piston_pos_min = piston_range.get("min", 0.0)
        piston_pos_max = piston_range.get("max", 0.8)

        logging.info(f"{CONFIG_FILE} を正常に読み込みました。")

    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.warning(f"{CONFIG_FILE} が読み込めませんでした ({e})。デフォルト設定で新しいファイルを作成します。")
        save_config() # デフォルト値で新しい設定ファイルを作成

class PulsingManager:
    def __init__(self, page: ft.Page, interval: float = 0.8):
        self.page = page
        self.interval = interval
        self.controls = set()
        self._task = None
        self._is_pulsed = False  # 点滅の状態（暗い状態か）を管理するフラグ

    async def _run(self):
        try:
            while self.controls:
                # ループ開始時間を記録
                loop_start_time = asyncio.get_event_loop().time()

                # 点滅の状態を切り替え
                self._is_pulsed = not self._is_pulsed
                opacity = 0.4 if self._is_pulsed else 1.0

                for control in self.controls:
                    control.opacity = opacity

                # ページセッションが有効な場合のみUIを更新
                if self.page.session:
                    try:
                        self.page.update()
                    except Exception as e:
                        logging.warning(f"PulsingManager: page.update() failed, stopping pulse. Error: {e}")
                        break  # 更新に失敗したらループを抜ける

                # 次の実行時刻までの待機時間を計算
                # これにより、処理時間を含めて全体で 'interval' 秒待機するようになります
                elapsed = asyncio.get_event_loop().time() - loop_start_time
                sleep_duration = max(0, self.interval - elapsed)
                await asyncio.sleep(sleep_duration)

        except asyncio.CancelledError:
            pass  # タスクのキャンセルは正常な終了処理
        finally:
            # 終了時に必ずコントロールの表示を元に戻す
            for control in self.controls:
                control.opacity = 1.0
            if self.page.session:
                try:
                    self.page.update()
                except Exception:
                    # ページが閉じられている場合はエラーになるため何もしない
                    pass
            self._task = None

    def add(self, *controls: ft.Text):
        self.controls.update(controls)
        # タスクが未実行または完了している場合、新しいタスクを開始する
        if not self._task or self._task.done():
            self._is_pulsed = False # 開始時の状態をリセット
            self._task = asyncio.create_task(self._run())
            background_tasks.add(self._task)

    def remove(self, *controls: ft.Text):
        for control in controls:
            if control in self.controls:
                self.controls.remove(control)
                control.opacity = 1.0  # 即座に表示を元に戻す

        # 点滅対象がなくなったらタスクをキャンセル
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

# --- デバイス制御タスク---

async def piston_worker():
    is_homed = True
    target_position = piston_pos_max
    while not is_shutting_down:
        try:
            if current_piston_mode > 0 and linear_actuator:
                is_homed = False
                interval = PISTON_SPEED_MAP.get(current_piston_mode, 1.0)
                await linear_actuator.command(position=target_position, duration=int(interval * 1000))
                target_position = piston_pos_min if target_position == piston_pos_max else piston_pos_max
                await asyncio.sleep(interval)
            elif current_piston_mode == 0 and not is_homed and linear_actuator:
                logging.info("ピストンモードがオフのため、ホームポジションに戻ります。")
                await linear_actuator.command(position=0.5, duration=700)
                is_homed = True
                target_position = piston_pos_max
                await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"ピストン制御中にエラー: {e}")
            await asyncio.sleep(1)

async def vibe_worker():
    last_sent_strength = -1.0
    while not is_shutting_down:
        try:
            target_strength = VIBE_STRENGTH_MAP.get(current_vibe_mode, 0.0)
            if vibrator_actuator and target_strength != last_sent_strength:
                await vibrator_actuator.command(speed=target_strength)
                last_sent_strength = target_strength
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"振動制御中にエラー: {e}")

async def game_websocket_listener(page: ft.Page, game_status: ft.Text, piston_mode_text: ft.Text, vibe_mode_text: ft.Text, pulsing_manager: PulsingManager):
    global current_piston_mode, current_vibe_mode
    while not is_shutting_down:
        try:
            game_status.value = "Waiting..."
            game_status.color = ft.Colors.YELLOW
            pulsing_manager.add(game_status)
            async with websockets.connect(GAME_WS_URL) as websocket:
                pulsing_manager.remove(game_status)
                logging.info(f"ゲームMOD ({GAME_WS_URL}) に接続しました。")
                game_status.value = "Connected"
                game_status.color = ft.Colors.GREEN
                page.update()
                async for message in websocket:
                    data = json.loads(message)
                    new_piston_mode = data.get("piston", 0)
                    new_vibe_mode = data.get("vibe", 0)

                    if new_piston_mode != current_piston_mode or new_vibe_mode != current_vibe_mode:
                        piston_mode_text.value = f"Piston Mode: {new_piston_mode}"
                        vibe_mode_text.value = f"Vibe Mode: {new_vibe_mode}"
                        current_piston_mode = new_piston_mode
                        current_vibe_mode = new_vibe_mode
                        page.update()
        except asyncio.CancelledError:
            break
        except Exception:
            logging.warning(f"ゲームMODへの接続に失敗。5秒後に再接続します。")
            await asyncio.sleep(1)

    pulsing_manager.remove(game_status)

async def intiface_manager(page: ft.Page, intiface_status: ft.Text, game_status: ft.Text, piston_group: ft.RadioGroup, vibe_group: ft.RadioGroup, game_listener_args: tuple, pulsing_manager: PulsingManager):
    global linear_actuator, vibrator_actuator, linear_devices, vibrator_devices, cli

    is_retrying = False
    while not is_shutting_down:
        active_workers = set()
        try:
            cli = Client("Python Bridge")
            if not is_retrying:
                intiface_status.value = "Connecting..."
                intiface_status.color = ft.Colors.YELLOW
                pulsing_manager.add(intiface_status)

            connector = WebsocketConnector(INTIFACE_WS_URL)
            await cli.connect(connector)

            is_retrying = False
            pulsing_manager.remove(intiface_status)
            intiface_status.value = "Connected"
            intiface_status.color = ft.Colors.GREEN
            page.update()

            async def rescan_and_update_ui():
                global linear_actuator, vibrator_actuator

                intiface_status.value = "Scanning for devices..."
                intiface_status.color = ft.Colors.BLUE
                pulsing_manager.add(intiface_status)
                await cli.start_scanning()
                await asyncio.sleep(3)
                await cli.stop_scanning()
                pulsing_manager.remove(intiface_status)
                linear_devices.clear()
                vibrator_devices.clear()
                for device in cli.devices.values():
                    if device.removed: continue
                    if hasattr(device, 'linear_actuators') and device.linear_actuators:
                        linear_devices[device.index] = device
                    if hasattr(device, 'vibrator_actuators') and device.vibrator_actuators:
                        vibrator_devices[device.index] = device
                piston_group.content.controls.clear()
                vibe_group.content.controls.clear()
                new_linear_actuator = None
                if not linear_devices:
                    piston_group.content.controls.append(ft.Text("No piston devices found."))
                else:
                    for index, device in linear_devices.items():
                        piston_group.content.controls.append(ft.Radio(value=str(index), label=device.name))
                    first_device_index_str = str(list(linear_devices.keys())[0])
                    piston_group.value = piston_group.value if piston_group.value and int(piston_group.value) in linear_devices else first_device_index_str
                    new_linear_actuator = linear_devices[int(piston_group.value)].linear_actuators[0]
                linear_actuator = new_linear_actuator
                new_vibrator_actuator = None
                if not vibrator_devices:
                    vibe_group.content.controls.append(ft.Text("No vibe devices found."))
                else:
                    for index, device in vibrator_devices.items():
                        vibe_group.content.controls.append(ft.Radio(value=str(index), label=device.name))
                    first_device_index_str = str(list(vibrator_devices.keys())[0])
                    vibe_group.value = vibe_group.value if vibe_group.value and int(vibe_group.value) in vibrator_devices else first_device_index_str
                    new_vibrator_actuator = vibrator_devices[int(vibe_group.value)].vibrator_actuators[0]
                vibrator_actuator = new_vibrator_actuator
                if linear_actuator or vibrator_actuator:
                    intiface_status.value = "Device connected"
                    intiface_status.color = ft.Colors.GREEN
                else:
                    intiface_status.value = "No usable devices found"
                    intiface_status.color = ft.Colors.ORANGE
                page.update()

            await rescan_and_update_ui()
            piston_task = asyncio.create_task(piston_worker())
            vibe_task = asyncio.create_task(vibe_worker())
            game_task = asyncio.create_task(game_websocket_listener(*game_listener_args, pulsing_manager))

            async def periodic_scanner():
                known_device_ids = set(d.index for d in cli.devices.values() if not d.removed)
                while True:
                    await asyncio.sleep(10)
                    if not cli or not cli.connected: continue
                    await cli.start_scanning()
                    await asyncio.sleep(2)
                    await cli.stop_scanning()

                    current_device_ids = set(d.index for d in cli.devices.values() if not d.removed)
                    if current_device_ids != known_device_ids:
                        logging.info("デバイスリストが変更されました。UIを更新します。")
                        await rescan_and_update_ui()
                        known_device_ids = current_device_ids
            scanner_task = asyncio.create_task(periodic_scanner())

            new_workers = {piston_task, vibe_task, game_task, scanner_task}
            active_workers.update(new_workers)
            background_tasks.update(new_workers)

            await asyncio.gather(*new_workers)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Intifaceマネージャーでエラー: {e}")
        finally:
            for task in active_workers:
                if not task.done(): task.cancel()
            await asyncio.gather(*active_workers, return_exceptions=True)
            background_tasks.difference_update(active_workers)

            linear_actuator = None
            vibrator_actuator = None

            if not is_shutting_down:
                is_retrying = True
                # 点滅をリセットせず、テキストだけ更新して点滅を継続させる
                intiface_status.value = "Disconnected. Retrying..."
                intiface_status.color = ft.Colors.ORANGE
                game_status.value = "Paused (Waiting for Intiface)..."
                game_status.color = ft.Colors.YELLOW

                # 点滅対象に追加し、継続させる
                pulsing_manager.add(intiface_status, game_status)

                piston_group.content.controls.clear()
                vibe_group.content.controls.clear()
                piston_group.content.controls.append(ft.Text("Waiting for Intiface..."))
                vibe_group.content.controls.append(ft.Text("Waiting for Intiface..."))
                page.update()
                await asyncio.sleep(3)

async def main(page: ft.Page):
    # アプリケーション起動時に設定を読み込む
    load_config()

    global piston_pos_min, piston_pos_max
    page.title = "Toy Controller"
    page.window.width = 650
    page.window.height = 1000
    page.window.maximizable = False
    page.window.resizable = False
    page.scroll = ft.ScrollMode.ADAPTIVE
    page.padding = 20
    pulsing_manager = PulsingManager(page)
    intiface_status_label = ft.Text("Intiface:", size=16, weight=ft.FontWeight.BOLD)
    intiface_status_value = ft.Text("Initializing...", size=16, animate_opacity=ft.Animation(500, ft.AnimationCurve.EASE_IN_OUT))
    game_status_label = ft.Text("Game:", size=16, weight=ft.FontWeight.BOLD)
    game_status_value = ft.Text("Waiting for Intiface...", size=16, animate_opacity=ft.Animation(500, ft.AnimationCurve.EASE_IN_OUT))
    piston_selection_title = ft.Text("Piston Device:", weight=ft.FontWeight.BOLD)
    piston_selection_group = ft.RadioGroup(content=ft.Column())
    vibe_selection_title = ft.Text("Vibration Device:", weight=ft.FontWeight.BOLD)
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
    range_settings_title = ft.Text("Piston Range Settings", size=16, weight=ft.FontWeight.BOLD)
    min_pos_text = ft.Text(f"Min Position: {piston_pos_min:.2f}")
    min_pos_slider = ft.Slider(min=0.0, max=1.0, value=piston_pos_min, width=200, divisions=100)
    max_pos_text = ft.Text(f"Max Position: {piston_pos_max:.2f}")
    max_pos_slider = ft.Slider(min=0.0, max=1.0, value=piston_pos_max, width=200, divisions=100)
    vibe_settings_title = ft.Text("Vibe Strength Settings", size=16, weight=ft.FontWeight.BOLD)
    vibe_1_text = ft.Text(f"Mode 1 (Low) Strength: {VIBE_STRENGTH_MAP[1]:.1f}")
    vibe_1_slider = ft.Slider(min=0.0, max=1.0, value=VIBE_STRENGTH_MAP[1], width=200, divisions=10)
    vibe_2_text = ft.Text(f"Mode 2 (High) Strength: {VIBE_STRENGTH_MAP[2]:.1f}")
    vibe_2_slider = ft.Slider(min=0.0, max=1.0, value=VIBE_STRENGTH_MAP[2], width=200, divisions=10)

    def on_piston_device_selected(e):
        global linear_actuator, linear_devices
        device_index = int(e.control.value)
        if selected_device := linear_devices.get(device_index):
            linear_actuator = selected_device.linear_actuators[0]
            logging.info(f"Piston device set to: {selected_device.name}")

    def on_vibe_device_selected(e):
        global vibrator_actuator, vibrator_devices
        device_index = int(e.control.value)
        if selected_device := vibrator_devices.get(device_index):
            vibrator_actuator = selected_device.vibrator_actuators[0]
            logging.info(f"Vibration device set to: {selected_device.name}")

    piston_selection_group.on_change = on_piston_device_selected
    vibe_selection_group.on_change = on_vibe_device_selected

    # スライダーの操作完了時に設定を保存するイベントハンドラ
    def save_on_change_end(e):
        save_config()

    def on_speed_slider_change(e, mode, text_control, text_prefix):
        new_val = round(e.control.value, 1)
        PISTON_SPEED_MAP[mode] = new_val
        text_control.value = f"{text_prefix}: {new_val:.1f}s"
        page.update()

    def on_min_pos_change(e):
        global piston_pos_min, piston_pos_max
        new_min = round(e.control.value, 2)
        if new_min > piston_pos_max:
            piston_pos_max = new_min
            max_pos_slider.value = new_min
            max_pos_text.value = f"Max Position: {new_min:.2f}"
        piston_pos_min = new_min
        min_pos_text.value = f"Min Position: {new_min:.2f}"
        page.update()

    def on_max_pos_change(e):
        global piston_pos_min, piston_pos_max
        new_max = round(e.control.value, 2)
        if new_max < piston_pos_min:
            piston_pos_min = new_max
            min_pos_slider.value = new_max
            min_pos_text.value = f"Min Position: {new_max:.2f}"
        piston_pos_max = new_max
        max_pos_text.value = f"Max Position: {new_max:.2f}"
        page.update()

    def on_vibe_strength_slider_change(e, mode, text_control):
        new_val = round(e.control.value, 2)
        VIBE_STRENGTH_MAP[mode] = new_val
        text_control.value = f"Mode {mode} Strength: {new_val:.1f}"
        page.update()
        
    # --- スライダーイベントの割り当て ---
    # on_changeはUIのテキスト更新のみ
    speed_1_slider.on_change = lambda e: on_speed_slider_change(e, 1, speed_1_text, "Mode 1 (Slow) Interval")
    speed_2_slider.on_change = lambda e: on_speed_slider_change(e, 2, speed_2_text, "Mode 2 (Medium) Interval")
    speed_3_slider.on_change = lambda e: on_speed_slider_change(e, 3, speed_3_text, "Mode 3 (Fast) Interval")
    min_pos_slider.on_change = on_min_pos_change
    max_pos_slider.on_change = on_max_pos_change
    vibe_1_slider.on_change = lambda e: on_vibe_strength_slider_change(e, 1, vibe_1_text)
    vibe_2_slider.on_change = lambda e: on_vibe_strength_slider_change(e, 2, vibe_2_text)

    # on_change_endでファイル保存を実行（重量）
    speed_1_slider.on_change_end = save_on_change_end
    speed_2_slider.on_change_end = save_on_change_end
    speed_3_slider.on_change_end = save_on_change_end
    min_pos_slider.on_change_end = save_on_change_end
    max_pos_slider.on_change_end = save_on_change_end
    vibe_1_slider.on_change_end = save_on_change_end
    vibe_2_slider.on_change_end = save_on_change_end

    page.add(
        ft.Column(
            controls=[
                ft.Row(
                    [
                        ft.Column(
                            [intiface_status_label, game_status_label],
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.END,
                        ),
                        ft.Column(
                            [intiface_status_value, game_status_value],
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.START,
                        ),
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
                                ft.Column([vibe_selection_title, vibe_selection_group], expand=True),
                            ],
                            spacing=20,
                            vertical_alignment=ft.CrossAxisAlignment.START
                        ),
                        padding=20
                    )
                ),
                ft.Row(
                    controls=[
                        ft.Card(
                            expand=True,
                            content=ft.Container(
                                content=ft.Column(
                                    [
                                        ft.ListTile(title=ft.Text("Piston Settings", weight=ft.FontWeight.BOLD, size=18), leading=ft.Icon(ft.Icons.LINEAR_SCALE)),
                                        piston_mode_display,
                                        ft.Divider(height=5),
                                        speed_settings_title,
                                        speed_1_text, speed_1_slider,
                                        speed_2_text, speed_2_slider,
                                        speed_3_text, speed_3_slider,
                                        ft.Divider(height=5),
                                        range_settings_title,
                                        min_pos_text, min_pos_slider,
                                        max_pos_text, max_pos_slider,
                                    ],
                                    spacing=5
                                ),
                                padding=15
                            )
                        ),
                        ft.Card(
                            expand=True,
                            content=ft.Container(
                                content=ft.Column(
                                    [
                                        ft.ListTile(title=ft.Text("Vibration Settings", weight=ft.FontWeight.BOLD, size=18), leading=ft.Icon(ft.Icons.VIBRATION)),
                                        vibe_mode_display,
                                        ft.Divider(height=5),
                                        vibe_settings_title,
                                        vibe_1_text, vibe_1_slider,
                                        vibe_2_text, vibe_2_slider,
                                    ],
                                    spacing=5
                                ),
                                padding=15
                            )
                        )
                    ],
                    spacing=20,
                    vertical_alignment=ft.CrossAxisAlignment.START
                )
            ],
            spacing=20
        )
    )

    async def on_disconnect_handler(e):
        global cli, is_shutting_down
        if is_shutting_down: return
        is_shutting_down = True
        
        # アプリ終了時に最終的な設定を保存する
        logging.info("最終設定を保存します。")
        save_config()

        logging.info("切断イベント受信。クリーンアップ処理を開始します。")
        pulsing_manager.clear()

        tasks_to_cancel = list(background_tasks)
        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        if cli and cli.connected:
            try:
                if linear_actuator:
                    logging.info("ピストンを中央に戻します。")
                    await linear_actuator.command(position=0.5, duration=700)
                await cli.disconnect()
            except Exception as ex:
                logging.error(f"クリーン切断中のエラー: {ex}")
        logging.info("クリーンアップ完了。")

    page.on_disconnect = on_disconnect_handler

    game_args = (page, game_status_value, piston_mode_display, vibe_mode_display)
    manager_args = (page, intiface_status_value, game_status_value, piston_selection_group, vibe_selection_group, game_args, pulsing_manager)

    main_task = asyncio.create_task(intiface_manager(*manager_args))
    background_tasks.add(main_task)

    try:
        await main_task
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    ft.app(target=main)
