package procurement

import "github.com/XUJiahua/enterprise_agent/kernel"

// SetAfterLock installs the test hook; pass nil to remove it.
func SetAfterLock(f func(kernel.Ref)) { afterLock = f }
