//go:build unix

package metrics

import (
	"os"
	"path/filepath"
)

// updateProcessMetrics collects platform-specific process metrics on Unix systems.
func updateProcessMetrics() {
	// Count open file descriptors by reading /proc/self/fd/
	// This works on Linux; on macOS it might not be available
	fdDir := "/proc/self/fd"
	if entries, err := os.ReadDir(fdDir); err == nil {
		ProcessOpenFDs.Set(float64(len(entries)))
	} else {
		// Fallback: try /dev/fd which works on macOS
		fdDir = "/dev/fd"
		if entries, err := os.ReadDir(fdDir); err == nil {
			ProcessOpenFDs.Set(float64(len(entries)))
		}
	}
}

// CountFilesInDir counts files matching pattern (used for FD counting fallback).
func countFilesInDir(dir string) int {
	matches, err := filepath.Glob(filepath.Join(dir, "*"))
	if err != nil {
		return 0
	}
	return len(matches)
}
