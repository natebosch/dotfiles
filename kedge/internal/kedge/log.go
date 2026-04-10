package kedge

import (
	"log/slog"
	"os"
	"path/filepath"
)

// Log is the system audit logger for kedge.
var Log *slog.Logger

func init() {
	logDir := os.Getenv("XDG_RUNTIME_DIR")
	if logDir == "" {
		logDir = "/tmp"
	}
	logPath := filepath.Join(logDir, "kedge.log")

	// Rotate if > 5MB to prevent RAM exhaustion in tmpfs
	const maxSizeBytes = 5 * 1024 * 1024
	if info, errStat := os.Stat(logPath); errStat == nil && info.Size() > maxSizeBytes {
		_ = os.Rename(logPath, logPath+".1")
	}

	//nolint:gosec // 0600 permission is intentionally restrictive for audit logs
	f, errOpen := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if errOpen != nil {
		// Fallback to stdout/stderr if XDG_RUNTIME_DIR is unwritable
		Log = slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
		return
	}

	handler := slog.NewJSONHandler(f, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})
	Log = slog.New(handler)
}
