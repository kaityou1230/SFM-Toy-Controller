# SFM-Toy-Controller

A tool to connect SecretFlasherManaka with Intiface Central, allowing for real-time control of supported toys.

This application listens for in-game events sent by the [SFMToyWebsocket](https://github.com/Henry1887/SFMToyWebsocket) BepInEx plugin (created by Henry1887) and controls devices connected to Intiface.

This tool has been tested and works with the OSR2 and The Handy.  
Vibration support is also included, but since I don't have a vibe device, it's currently untested. If you try it, please let me know if it works!

## Features
+ Real-time Game Integration: Automatically controls piston and vibration functions based on in-game events.

+ Detailed GUI Settings: Finely adjust piston speed, stroke range, and vibration strength through an intuitive user interface.

+ Persistent Settings: Your adjustments are automatically saved to config.json and will be loaded the next time you start the app.

+ Automatic Device Scanning: Automatically detects and lists devices connected to Intiface.

## Prerequisites
To use this tool, you will need all of the following software installed and configured:

+ [BepInEx for IL2CPP (x64)](https://builds.bepinex.dev/projects/bepinex_be)

+ [Intiface Central](https://intiface.com/central/)


## Installation
1. **Install BepInEx**: Download and install the latest **BepInEx for IL2CPP (x64)** into your main game folder.

2. Download the latest .zip file from the releases page.

3. Unzip the file. Place Toy_Controller.exe anywhere on your PC.

4. Copy the BepInEx folder from the zip file and place it directly into your main game folder.

## Usage
1. Launch Intiface Central and ensure its server is running.

2. Open the extracted folder and run Toy_Controller.exe (or a similarly named executable file).

3. The application will launch, automatically connect to Intiface, and scan for devices.

4. Launch SecretFlasherManaka.

5. Once the game is running, the status in this application should change to "Connected".

6. You're all set! Your devices will now react to gameplay.
