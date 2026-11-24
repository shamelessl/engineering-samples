using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

// Read-only sample. The only handle queried belongs to this process.
class ProcessIdentity {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetProcessTimes(SafeProcessHandle process, out long creation,
        out long exit, out long kernel, out long user);

    static int Main() {
        using (var self = Process.GetCurrentProcess()) {
            long creation, exit, kernel, user;
            if (!GetProcessTimes(self.SafeHandle, out creation, out exit, out kernel, out user))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            long managedStart = self.StartTime.ToUniversalTime().ToFileTimeUtc();
            if (creation != managedStart) return 1;
            Console.WriteLine("Same process verified: PID={0}, created={1:o}", self.Id,
                              DateTime.FromFileTimeUtc(creation));
            return 0;
        }
    }
}
