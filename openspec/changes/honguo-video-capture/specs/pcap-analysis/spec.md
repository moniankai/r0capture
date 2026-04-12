## ADDED Requirements

### Requirement: Parse PCAP file

The system SHALL parse PCAP files and extract HTTP/HTTPS packets.

#### Scenario: Successful PCAP parsing
- **WHEN** user provides a valid PCAP file
- **THEN** system reads all packets
- **AND** extracts HTTP/HTTPS traffic

#### Scenario: Invalid PCAP file
- **WHEN** user provides an invalid or corrupted PCAP file
- **THEN** system displays error message "Invalid PCAP file format"
- **AND** exits gracefully

### Requirement: Extract video URLs

The system SHALL identify and extract video-related URLs from HTTP traffic.

#### Scenario: M3U8 URL extraction
- **WHEN** PCAP contains M3U8 requests
- **THEN** system extracts all M3U8 URLs
- **AND** removes duplicates

#### Scenario: TS segment URL extraction
- **WHEN** PCAP contains TS segment requests
- **THEN** system extracts TS URLs
- **AND** groups them by M3U8 playlist

#### Scenario: MP4 direct link extraction
- **WHEN** PCAP contains MP4 file requests
- **THEN** system extracts MP4 URLs
- **AND** marks them as direct download

### Requirement: Identify video format

The system SHALL automatically detect the video streaming format.

#### Scenario: HLS format detection
- **WHEN** URL ends with .m3u8
- **THEN** system identifies format as HLS
- **AND** prepares M3U8 parser

#### Scenario: MP4 format detection
- **WHEN** URL ends with .mp4
- **THEN** system identifies format as MP4
- **AND** prepares direct downloader

#### Scenario: Unknown format
- **WHEN** URL format is unrecognized
- **THEN** system logs warning
- **AND** attempts generic HTTP download

### Requirement: Extract request metadata

The system SHALL extract HTTP headers and parameters from video requests.

#### Scenario: Extract authentication headers
- **WHEN** video request contains Authorization header
- **THEN** system extracts and stores the header
- **AND** uses it for subsequent downloads

#### Scenario: Extract URL parameters
- **WHEN** video URL contains query parameters (token, signature)
- **THEN** system extracts all parameters
- **AND** preserves them for download requests

### Requirement: Detect encryption

The system SHALL detect if video content is encrypted.

#### Scenario: AES-128 encryption detected
- **WHEN** M3U8 contains #EXT-X-KEY tag
- **THEN** system identifies encryption as AES-128
- **AND** extracts key URI and IV

#### Scenario: No encryption
- **WHEN** M3U8 does not contain #EXT-X-KEY
- **THEN** system marks video as unencrypted
- **AND** proceeds with direct download

### Requirement: Generate download report

The system SHALL generate a summary report of extracted video URLs.

#### Scenario: Successful report generation
- **WHEN** PCAP analysis is complete
- **THEN** system generates JSON report with:
  - Total videos found
  - Video formats
  - Encryption status
  - Download URLs
- **AND** saves report to file

#### Scenario: No videos found
- **WHEN** PCAP contains no video traffic
- **THEN** system displays message "No video URLs found in PCAP"
- **AND** suggests checking if correct app was captured
