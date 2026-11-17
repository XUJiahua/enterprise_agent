package procurement

import (
	"database/sql"
	"errors"
	"fmt"

	"github.com/golang-migrate/migrate/v4"
	migratepgx "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	_ "github.com/jackc/pgx/v5/stdlib"
)

// Migrate applies the pending product migrations as the admin role at url.
// golang-migrate holds a PostgreSQL advisory lock while it runs and refuses a
// dirty database (a migration that failed halfway) until an operator repairs
// it. It returns the resulting version and whether anything was applied.
//
// version/dirty say which files ran, not that their content is unchanged: the
// content of published migrations is protected by `appctl verify --base`.
func Migrate(url string) (version uint, applied bool, err error) {
	db, err := sql.Open("pgx", url)
	if err != nil {
		return 0, false, err
	}
	defer db.Close()
	m, err := migrator(db)
	if err != nil {
		return 0, false, err
	}
	switch err = m.Up(); {
	case errors.Is(err, migrate.ErrNoChange):
	case err != nil:
		return 0, false, err
	default:
		applied = true
	}
	version, dirty, err := m.Version()
	if err == nil && dirty {
		err = fmt.Errorf("database is dirty at version %d", version)
	}
	return version, applied, err
}

func migrator(db *sql.DB) (*migrate.Migrate, error) {
	src, err := iofs.New(Migrations, "migrations")
	if err != nil {
		return nil, err
	}
	drv, err := migratepgx.WithInstance(db, &migratepgx.Config{MigrationsTable: "procurement_schema_migrations"})
	if err != nil {
		return nil, err
	}
	return migrate.NewWithInstance("iofs", src, "pgx5", drv)
}
