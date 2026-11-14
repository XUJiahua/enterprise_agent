package policy

import (
	"fmt"
	"strings"
	"unicode"
)

// CheckMigration admits only the POC's additive CREATE TABLE / CREATE INDEX
// subset. Everything else requires a reviewed extension of the verifier. This is
// a conservative lexer/allowlist, not a PostgreSQL semantic compatibility proof.
func CheckMigration(sql string) error {
	statements := [][]string{{}}
	for i := 0; i < len(sql); {
		c := sql[i]
		if unicode.IsSpace(rune(c)) {
			i++
			continue
		}
		if c == '-' && i+1 < len(sql) && sql[i+1] == '-' {
			for i < len(sql) && sql[i] != '\n' {
				i++
			}
			continue
		}
		if c == '/' && i+1 < len(sql) && sql[i+1] == '*' {
			depth := 1
			i += 2
			for i < len(sql) && depth > 0 {
				if i+1 < len(sql) && sql[i:i+2] == "/*" {
					depth++
					i += 2
				} else if i+1 < len(sql) && sql[i:i+2] == "*/" {
					depth--
					i += 2
				} else {
					i++
				}
			}
			if depth != 0 {
				return fmt.Errorf("unterminated SQL comment")
			}
			continue
		}
		if c == '$' || c == '\\' {
			return fmt.Errorf("unsupported SQL escape or dollar quote")
		}
		if c == '\'' || c == '"' {
			quote := c
			i++
			closed := false
			for i < len(sql) {
				if sql[i] == '\\' {
					return fmt.Errorf("unsupported SQL escape")
				}
				if sql[i] == quote {
					if i+1 < len(sql) && sql[i+1] == quote {
						i += 2
						continue
					}
					i++
					closed = true
					break
				}
				i++
			}
			if !closed {
				return fmt.Errorf("unterminated SQL quote")
			}
			statements[len(statements)-1] = append(statements[len(statements)-1], "<quoted>")
			continue
		}
		if c == ';' {
			statements = append(statements, []string{})
			i++
			continue
		}
		if unicode.IsLetter(rune(c)) || c == '_' {
			start := i
			for i < len(sql) && (unicode.IsLetter(rune(sql[i])) || unicode.IsDigit(rune(sql[i])) || sql[i] == '_') {
				i++
			}
			statements[len(statements)-1] = append(statements[len(statements)-1], strings.ToUpper(sql[start:i]))
			continue
		}
		i++
	}
	for _, tokens := range statements {
		if len(tokens) == 0 {
			continue
		}
		if len(tokens) < 3 || tokens[0] != "CREATE" || (tokens[1] != "TABLE" && tokens[1] != "INDEX") {
			return fmt.Errorf("only additive CREATE TABLE / CREATE INDEX supported; migration-owner review required")
		}
		for _, word := range tokens {
			switch word {
			case "AS", "SELECT", "DROP", "ALTER", "TRUNCATE", "DELETE", "UPDATE", "EXECUTE", "DO":
				return fmt.Errorf("unsupported SQL construct %s; migration-owner review required", word)
			}
		}
	}
	return nil
}
