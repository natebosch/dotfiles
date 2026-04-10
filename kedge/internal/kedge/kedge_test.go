package kedge

import (
	"testing"
)

func TestKedgeIDFormatting(t *testing.T) {
	tests := []struct {
		ki       KedgeID
		expected string
	}{
		{KedgeID{TypeRepo, "my-repo"}, "R_my-repo"},
		{KedgeID{TypeProject, "proj-1"}, "P_proj-1"},
		{KedgeID{TypeSession, "some_session"}, "S_some_session"},
	}

	for _, tc := range tests {
		if got := tc.ki.String(); got != tc.expected {
			t.Errorf("KedgeID(%v, %s).String() = %s; want %s", tc.ki.Type, tc.ki.Name, got, tc.expected)
		}
	}
}

func TestKedgeIDTmuxSessionName(t *testing.T) {
	tests := []struct {
		ki       KedgeID
		expected string
	}{
		{KedgeID{TypeRepo, "my-repo"}, "R_my-repo"},
		{KedgeID{TypeProject, "proj-1"}, "P_proj-1"},
		{KedgeID{TypeSession, "some_session"}, "some_session"},
	}

	for _, tc := range tests {
		if got := tc.ki.TmuxSessionName(); got != tc.expected {
			t.Errorf("KedgeID(%v, %s).TmuxSessionName() = %s; want %s", tc.ki.Type, tc.ki.Name, got, tc.expected)
		}
	}
}

func TestParseKedgeID(t *testing.T) {
	tests := []struct {
		input    string
		expected KedgeID
		wantErr  bool
	}{
		{"R_my-repo", KedgeID{TypeRepo, "my-repo"}, false},
		{"G_my-repo", KedgeID{TypeRepo, "my-repo"}, false},
		{"P_proj-1", KedgeID{TypeProject, "proj-1"}, false},
		{"S_some_session", KedgeID{TypeSession, "some_session"}, false},
		{"some_session", KedgeID{TypeSession, "some_session"}, false},
		{"X_invalid", KedgeID{TypeSession, "X_invalid"}, false},
		{"no-underscore", KedgeID{TypeSession, "no-underscore"}, false},
		{"R_", KedgeID{}, true},
		{"R_invalid space", KedgeID{}, true},
		{"invalid space", KedgeID{}, true},
		{"R_invalid.dot", KedgeID{}, true},
	}

	for _, tc := range tests {
		got, err := ParseKedgeID(tc.input)
		if (err != nil) != tc.wantErr {
			t.Errorf("ParseKedgeID(%s) error = %v; wantErr %v", tc.input, err, tc.wantErr)
			continue
		}
		if !tc.wantErr && got != tc.expected {
			t.Errorf("ParseKedgeID(%s) = %v; want %v", tc.input, got, tc.expected)
		}
	}
}

func TestValidateName(t *testing.T) {
	tests := []struct {
		name    string
		isValid bool
	}{
		{"valid-name", true},
		{"valid_name", true},
		{"ValidName123", true},
		{"", false},
		{"invalid name", false},
		{"invalid/name", false},
		{"invalid@name", false},
		{"invalid.name", false},
	}

	for _, tc := range tests {
		ki := KedgeID{Type: TypeSession, Name: tc.name}
		err := ki.ValidateName()
		if (err == nil) != tc.isValid {
			t.Errorf("KedgeID.ValidateName(%s) valid = %v; want %v", tc.name, err == nil, tc.isValid)
		}
	}
}
