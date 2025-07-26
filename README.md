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

+ BepInEx

+ [SFMToyWebsocket](https://github.com/Henry1887/SFMToyWebsocket)

+ Intiface Central


## Installation
Please set up all the prerequisites listed above. It is crucial that SFMToyWebsocket is correctly installed in your game.

Navigate to the Releases page on the right side of this repository.

Download the latest .zip file.

Extract the downloaded zip file to a location of your choice.

## Usage
1. Launch Intiface Central and ensure its server is running.

2. Open the extracted folder and run Toy_Controller.exe (or a similarly named executable file).

3. The application will launch, automatically connect to Intiface, and scan for devices.

4. Launch SecretFlasherManaka.

5. Once the game is running, the status in this application should change to "Connected".

6. You're all set! Your devices will now react to gameplay.
