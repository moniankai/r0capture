## ADDED Requirements

### Requirement: Detect Android version

The system SHALL automatically detect the Android version of the connected device.

#### Scenario: Successful version detection
- **WHEN** user runs the setup script
- **THEN** system displays the detected Android version (e.g., "Android 9")

#### Scenario: Device not connected
- **WHEN** no device is connected via ADB
- **THEN** system displays error message "No device connected. Please connect device via USB and enable USB debugging."

### Requirement: Download appropriate Frida Server

The system SHALL download the correct Frida Server binary based on the detected Android version and device architecture.

#### Scenario: Download for Android 7-9
- **WHEN** Android version is 7, 8, or 9
- **THEN** system downloads frida-server-15.2.2-android-arm64

#### Scenario: Download for Android 10-12
- **WHEN** Android version is 10, 11, or 12
- **THEN** system downloads frida-server-16.5.2-android-arm64

#### Scenario: Download for Android 13+
- **WHEN** Android version is 13 or higher
- **THEN** system downloads frida-server-17.x-android-arm64

#### Scenario: Unsupported Android version
- **WHEN** Android version is below 7
- **THEN** system displays error message "Android version not supported. Minimum version: Android 7"

### Requirement: Install Frida Server to device

The system SHALL push the Frida Server binary to the device and set correct permissions.

#### Scenario: Successful installation
- **WHEN** Frida Server is downloaded
- **THEN** system pushes binary to /data/local/tmp/frida-server
- **AND** sets executable permission (chmod 755)

#### Scenario: Installation fails due to insufficient permissions
- **WHEN** device is not rooted
- **THEN** system displays error message "Root access required. Please root your device first."

### Requirement: Start Frida Server

The system SHALL start the Frida Server process on the device.

#### Scenario: Successful start
- **WHEN** Frida Server is installed
- **THEN** system starts frida-server in background
- **AND** verifies process is running

#### Scenario: Port already in use
- **WHEN** default port 27042 is already in use
- **THEN** system uses alternative port 27043
- **AND** displays message "Using alternative port: 27043"

### Requirement: Verify Frida connection

The system SHALL verify that Frida can connect to the device and list running processes.

#### Scenario: Successful verification
- **WHEN** Frida Server is running
- **THEN** system runs `frida-ps -U`
- **AND** displays list of running processes

#### Scenario: Connection fails
- **WHEN** Frida Server is not responding
- **THEN** system displays error message "Failed to connect to Frida Server. Please restart the device and try again."

### Requirement: Install Python dependencies

The system SHALL install all required Python packages.

#### Scenario: Successful installation
- **WHEN** user runs setup script
- **THEN** system installs: frida, frida-tools, loguru, click, scapy, m3u8, pycryptodome, ffmpeg-python
- **AND** verifies each package is importable

#### Scenario: Installation fails
- **WHEN** pip install fails for any package
- **THEN** system displays specific error message with package name
- **AND** provides troubleshooting suggestions
