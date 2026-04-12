## ADDED Requirements

### Requirement: Attach to target app

The system SHALL attach r0capture to the target application by app name.

#### Scenario: Successful attach
- **WHEN** user specifies app name "红果短剧"
- **THEN** system finds the app process
- **AND** attaches Frida to the process

#### Scenario: App not running
- **WHEN** target app is not running
- **THEN** system displays error message "App not running. Please start the app first."
- **AND** provides option to use spawn mode

#### Scenario: Multiple processes found
- **WHEN** multiple processes match the app name
- **THEN** system lists all matching processes
- **AND** prompts user to select the correct one

### Requirement: Capture SSL/TLS traffic

The system SHALL intercept and decrypt SSL/TLS traffic from the target app.

#### Scenario: Successful SSL interception
- **WHEN** app makes HTTPS request
- **THEN** system hooks SSL_read and SSL_write functions
- **AND** captures decrypted traffic

#### Scenario: SSL Pinning detected
- **WHEN** app uses SSL pinning
- **THEN** system bypasses certificate validation
- **AND** continues capturing traffic

### Requirement: Export traffic to PCAP

The system SHALL save captured traffic to PCAP file format.

#### Scenario: Successful PCAP export
- **WHEN** user specifies output file "honguo.pcap"
- **THEN** system writes all captured packets to the file
- **AND** file is readable by Wireshark

#### Scenario: Real-time PCAP writing
- **WHEN** traffic is being captured
- **THEN** system writes packets to PCAP in real-time
- **AND** file can be analyzed while capture is ongoing

### Requirement: Display traffic in console

The system SHALL display captured traffic in human-readable format in the console.

#### Scenario: Verbose mode enabled
- **WHEN** user enables verbose mode (-v flag)
- **THEN** system displays each request/response
- **AND** shows URL, method, headers, and body

#### Scenario: Quiet mode
- **WHEN** user does not enable verbose mode
- **THEN** system only displays summary statistics
- **AND** shows total packets captured

### Requirement: Filter traffic by protocol

The system SHALL support filtering captured traffic by protocol type.

#### Scenario: HTTP/HTTPS only
- **WHEN** user specifies HTTP filter
- **THEN** system only captures HTTP and HTTPS traffic
- **AND** ignores other protocols

#### Scenario: WebSocket traffic
- **WHEN** app uses WebSocket
- **THEN** system captures WebSocket handshake and messages
- **AND** displays in readable format

### Requirement: Handle capture interruption

The system SHALL gracefully handle capture interruption and save partial data.

#### Scenario: User stops capture
- **WHEN** user presses Ctrl+C
- **THEN** system stops capture gracefully
- **AND** saves all captured data to PCAP file
- **AND** displays capture statistics

#### Scenario: App crashes
- **WHEN** target app crashes during capture
- **THEN** system detects app termination
- **AND** saves captured data
- **AND** displays error message with crash details
