## ADDED Requirements

### Requirement: Parse M3U8 playlist

The system SHALL parse M3U8 playlist files and extract segment information.

#### Scenario: Master playlist parsing
- **WHEN** M3U8 is a master playlist with multiple quality levels
- **THEN** system lists all available qualities
- **AND** prompts user to select quality (or uses lowest by default)

#### Scenario: Media playlist parsing
- **WHEN** M3U8 is a media playlist
- **THEN** system extracts all TS segment URLs
- **AND** preserves segment order

### Requirement: Download TS segments

The system SHALL download all TS segments from the M3U8 playlist.

#### Scenario: Successful segment download
- **WHEN** all segments are available
- **THEN** system downloads each segment
- **AND** saves to temporary directory

#### Scenario: Segment download fails
- **WHEN** a segment download fails
- **THEN** system retries up to 3 times with exponential backoff
- **AND** logs failed segment for manual retry

### Requirement: Handle AES-128 encryption

The system SHALL decrypt AES-128 encrypted TS segments.

#### Scenario: Download encryption key
- **WHEN** M3U8 contains #EXT-X-KEY with URI
- **THEN** system downloads the key file
- **AND** stores key for decryption

#### Scenario: Decrypt TS segments
- **WHEN** TS segments are encrypted
- **THEN** system decrypts each segment using AES-128-CBC
- **AND** uses IV from M3U8 or segment sequence number

#### Scenario: Key download fails
- **WHEN** encryption key URI is inaccessible
- **THEN** system displays error message "Failed to download encryption key"
- **AND** attempts to extract key via Frida hook

### Requirement: Merge TS segments

The system SHALL merge downloaded TS segments into a single video file.

#### Scenario: Successful merge
- **WHEN** all segments are downloaded
- **THEN** system concatenates segments in order
- **AND** outputs MP4 file using ffmpeg

#### Scenario: Merge fails
- **WHEN** ffmpeg merge fails
- **THEN** system keeps individual TS files
- **AND** provides manual merge command

### Requirement: Support multi-threaded download

The system SHALL download multiple TS segments concurrently.

#### Scenario: Parallel download
- **WHEN** downloading TS segments
- **THEN** system uses thread pool with 5 workers
- **AND** downloads segments in parallel

#### Scenario: Rate limiting
- **WHEN** server returns 429 Too Many Requests
- **THEN** system reduces thread count to 2
- **AND** adds delay between requests

### Requirement: Resume interrupted downloads

The system SHALL support resuming interrupted HLS downloads.

#### Scenario: Resume from checkpoint
- **WHEN** download is interrupted
- **THEN** system saves progress checkpoint
- **AND** resumes from last completed segment on restart

#### Scenario: Verify existing segments
- **WHEN** resuming download
- **THEN** system verifies integrity of existing segments
- **AND** re-downloads corrupted segments

### Requirement: Display download progress

The system SHALL show real-time download progress.

#### Scenario: Progress bar display
- **WHEN** downloading segments
- **THEN** system displays progress bar with:
  - Percentage complete
  - Downloaded segments / Total segments
  - Current download speed
  - Estimated time remaining

#### Scenario: Completion notification
- **WHEN** download completes
- **THEN** system displays success message
- **AND** shows output file path and size
