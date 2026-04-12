## ADDED Requirements

### Requirement: Hook ExoPlayer

The system SHALL provide Frida script to hook ExoPlayer and extract video URLs.

#### Scenario: Hook MediaSource creation
- **WHEN** ExoPlayer creates MediaSource
- **THEN** Frida script intercepts the call
- **AND** logs video URL and format

#### Scenario: Hook player state changes
- **WHEN** ExoPlayer changes playback state
- **THEN** Frida script logs state transition
- **AND** captures video metadata

### Requirement: Hook AES decryption

The system SHALL provide Frida script to intercept AES decryption and extract keys.

#### Scenario: Hook javax.crypto.Cipher
- **WHEN** app uses javax.crypto.Cipher for decryption
- **THEN** Frida script intercepts doFinal method
- **AND** logs encryption key and IV

#### Scenario: Export decryption keys
- **WHEN** decryption key is captured
- **THEN** Frida script saves key to file
- **AND** displays key in hex format

### Requirement: Hook network requests

The system SHALL provide Frida script to intercept OkHttp network requests.

#### Scenario: Hook OkHttp requests
- **WHEN** app makes HTTP request via OkHttp
- **THEN** Frida script intercepts request
- **AND** logs URL, method, headers, and body

#### Scenario: Hook OkHttp responses
- **WHEN** app receives HTTP response
- **THEN** Frida script intercepts response
- **AND** logs status code, headers, and body

### Requirement: Bypass Frida detection

The system SHALL provide techniques to bypass Frida detection.

#### Scenario: Rename frida-server
- **WHEN** app checks for "frida-server" process
- **THEN** system renames binary to "system_server"
- **AND** app does not detect Frida

#### Scenario: Use non-standard port
- **WHEN** app checks for port 27042
- **THEN** system uses alternative port 27043
- **AND** connects Frida client to custom port

### Requirement: Hook custom video players

The system SHALL provide template for hooking custom video player implementations.

#### Scenario: Identify player class
- **WHEN** app uses custom video player
- **THEN** Frida script searches for player classes
- **AND** lists candidate classes for hooking

#### Scenario: Hook player methods
- **WHEN** user specifies player class
- **THEN** Frida script hooks all public methods
- **AND** logs method calls with arguments

### Requirement: Export hook results

The system SHALL export captured data from Frida hooks.

#### Scenario: Save to JSON file
- **WHEN** Frida hook captures data
- **THEN** system saves data to JSON file
- **AND** includes timestamp and data type

#### Scenario: Real-time display
- **WHEN** verbose mode is enabled
- **THEN** system displays hook results in console
- **AND** highlights important data (URLs, keys)
