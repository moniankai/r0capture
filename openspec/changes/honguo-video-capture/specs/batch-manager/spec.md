## ADDED Requirements

### Requirement: Manage download queue

The system SHALL maintain a queue of videos to download.

#### Scenario: Add videos to queue
- **WHEN** PCAP analysis finds multiple videos
- **THEN** system adds all videos to download queue
- **AND** displays queue size

#### Scenario: Remove duplicates
- **WHEN** same video URL appears multiple times
- **THEN** system detects duplicate by URL hash
- **AND** keeps only one entry in queue

### Requirement: Organize downloaded videos

The system SHALL organize downloaded videos by episode or series.

#### Scenario: Auto-detect episode number
- **WHEN** video URL or title contains episode number
- **THEN** system extracts episode number
- **AND** names file as "Episode_01.mp4"

#### Scenario: Create series folders
- **WHEN** multiple episodes of same series are detected
- **THEN** system creates folder with series name
- **AND** saves episodes inside folder

### Requirement: Track download status

The system SHALL track and persist download status for each video.

#### Scenario: Save download state
- **WHEN** download is in progress
- **THEN** system saves state to JSON file
- **AND** updates state on each segment completion

#### Scenario: Resume batch download
- **WHEN** batch download is interrupted
- **THEN** system loads previous state
- **AND** resumes from last incomplete video

### Requirement: Handle download failures

The system SHALL handle and retry failed downloads.

#### Scenario: Automatic retry
- **WHEN** video download fails
- **THEN** system retries up to 3 times
- **AND** logs failure reason

#### Scenario: Move to failed queue
- **WHEN** all retries are exhausted
- **THEN** system moves video to failed queue
- **AND** continues with next video

#### Scenario: Manual retry
- **WHEN** user requests retry of failed videos
- **THEN** system re-attempts all failed downloads
- **AND** displays retry results

### Requirement: Limit concurrent downloads

The system SHALL limit the number of concurrent video downloads.

#### Scenario: Respect concurrency limit
- **WHEN** downloading multiple videos
- **THEN** system limits to 2 concurrent video downloads
- **AND** queues remaining videos

#### Scenario: Adjust concurrency
- **WHEN** user specifies custom concurrency limit
- **THEN** system uses specified limit
- **AND** validates limit is between 1 and 10

### Requirement: Generate batch report

The system SHALL generate a summary report after batch download completes.

#### Scenario: Successful batch report
- **WHEN** batch download completes
- **THEN** system generates report with:
  - Total videos processed
  - Successful downloads
  - Failed downloads
  - Total size downloaded
  - Total time elapsed
- **AND** saves report to file

#### Scenario: Export failed list
- **WHEN** some downloads failed
- **THEN** system exports failed URLs to text file
- **AND** provides command to retry failed downloads
