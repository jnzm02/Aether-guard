//go:build !unix

package metrics

// updateProcessMetrics is a no-op on non-Unix platforms.
func updateProcessMetrics() {
	// Process metrics not available on this platform
	ProcessOpenFDs.Set(0)
}
