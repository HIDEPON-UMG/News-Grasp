Set-StrictMode -Version Latest

if (-not ('NewsGraspVerifiedFileBoundary' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;

public sealed class NewsGraspVerifiedFileData
{
    public byte[] Bytes { get; private set; }
    public string Sha256 { get; private set; }
    public uint NumberOfLinks { get; private set; }

    public NewsGraspVerifiedFileData(byte[] bytes, string sha256, uint numberOfLinks)
    {
        Bytes = bytes;
        Sha256 = sha256;
        NumberOfLinks = numberOfLinks;
    }
}

public static class NewsGraspVerifiedFileBoundary
{
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint DELETE = 0x00010000;
    private const uint FILE_READ_ATTRIBUTES = 0x00000080;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint CREATE_NEW = 1;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_ATTRIBUTE_NORMAL = 0x00000080;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const int FILE_DISPOSITION_INFO_CLASS = 4;
    private const int FILE_RENAME_INFORMATION_CLASS = 10;

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FILE_DISPOSITION_INFO
    {
        [MarshalAs(UnmanagedType.Bool)]
        public bool DeleteFile;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_STATUS_BLOCK
    {
        public IntPtr Status;
        public UIntPtr Information;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle fileHandle,
        out BY_HANDLE_FILE_INFORMATION fileInformation);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(
        SafeFileHandle fileHandle,
        StringBuilder filePath,
        uint filePathLength,
        uint flags);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool WriteFile(
        SafeFileHandle fileHandle,
        byte[] buffer,
        uint bytesToWrite,
        out uint bytesWritten,
        IntPtr overlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool FlushFileBuffers(SafeFileHandle fileHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetFileInformationByHandle(
        SafeFileHandle fileHandle,
        int fileInformationClass,
        ref FILE_DISPOSITION_INFO fileInformation,
        uint bufferSize);

    [DllImport("ntdll.dll")]
    private static extern int NtSetInformationFile(
        SafeFileHandle fileHandle,
        out IO_STATUS_BLOCK ioStatusBlock,
        IntPtr fileInformation,
        uint length,
        int fileInformationClass);

    [DllImport("ntdll.dll")]
    private static extern uint RtlNtStatusToDosError(int status);

    private static string NormalizePath(string path)
    {
        string value = path;
        if (value.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
        {
            value = @"\\" + value.Substring(8);
        }
        else if (value.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase))
        {
            value = value.Substring(4);
        }
        return Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static string GetFinalPath(SafeFileHandle handle)
    {
        StringBuilder buffer = new StringBuilder(512);
        uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
        if (length == 0)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "NEWS_GRASP_VERIFIED_HANDLE_FINAL_PATH_FAILED");
        }
        if (length >= buffer.Capacity)
        {
            buffer = new StringBuilder((int)length + 1);
            length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
            if (length == 0)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "NEWS_GRASP_VERIFIED_HANDLE_FINAL_PATH_FAILED");
            }
        }
        return NormalizePath(buffer.ToString());
    }

    private static BY_HANDLE_FILE_INFORMATION GetInformation(SafeFileHandle handle)
    {
        BY_HANDLE_FILE_INFORMATION information;
        if (!GetFileInformationByHandle(handle, out information))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "NEWS_GRASP_VERIFIED_HANDLE_INFORMATION_FAILED");
        }
        return information;
    }

    private static SafeFileHandle OpenVerifiedDirectory(string directoryPath)
    {
        string expected = NormalizePath(directoryPath);
        SafeFileHandle handle = CreateFileW(
            expected,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            IntPtr.Zero,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error, "NEWS_GRASP_VERIFIED_PARENT_OPEN_FAILED");
        }
        try
        {
            BY_HANDLE_FILE_INFORMATION information = GetInformation(handle);
            if ((information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
                (information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
            {
                throw new IOException("NEWS_GRASP_VERIFIED_PARENT_INVALID");
            }
            if (!String.Equals(GetFinalPath(handle), expected, StringComparison.OrdinalIgnoreCase))
            {
                throw new IOException("NEWS_GRASP_VERIFIED_PARENT_IDENTITY_MISMATCH");
            }
            return handle;
        }
        catch
        {
            handle.Dispose();
            throw;
        }
    }

    private static SafeFileHandle OpenVerifiedFile(
        string path,
        uint access,
        uint share,
        bool rejectReparse,
        bool requireSingleLink,
        string hardLinkError)
    {
        string expected = NormalizePath(path);
        SafeFileHandle handle = CreateFileW(
            expected,
            access,
            share,
            IntPtr.Zero,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error, "NEWS_GRASP_VERIFIED_FILE_OPEN_FAILED");
        }
        try
        {
            BY_HANDLE_FILE_INFORMATION information = GetInformation(handle);
            if ((information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
            {
                throw new IOException("NEWS_GRASP_VERIFIED_FILE_DIRECTORY_FORBIDDEN");
            }
            if (rejectReparse && (information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
            {
                throw new IOException("NEWS_GRASP_VERIFIED_SOURCE_REPARSE_FORBIDDEN");
            }
            if (requireSingleLink && information.NumberOfLinks != 1)
            {
                throw new IOException(hardLinkError);
            }
            if (!String.Equals(GetFinalPath(handle), expected, StringComparison.OrdinalIgnoreCase))
            {
                throw new IOException("NEWS_GRASP_VERIFIED_FILE_IDENTITY_MISMATCH");
            }
            return handle;
        }
        catch
        {
            handle.Dispose();
            throw;
        }
    }

    private static string HashBytes(byte[] bytes)
    {
        using (SHA256 sha256 = SHA256.Create())
        {
            byte[] hash = sha256.ComputeHash(bytes);
            StringBuilder result = new StringBuilder(hash.Length * 2);
            foreach (byte value in hash)
            {
                result.Append(value.ToString("x2"));
            }
            return result.ToString();
        }
    }

    private static void MarkDelete(SafeFileHandle handle)
    {
        FILE_DISPOSITION_INFO disposition = new FILE_DISPOSITION_INFO();
        disposition.DeleteFile = true;
        if (!SetFileInformationByHandle(
            handle,
            FILE_DISPOSITION_INFO_CLASS,
            ref disposition,
            (uint)Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO))))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "NEWS_GRASP_VERIFIED_DELETE_FAILED");
        }
    }

    private static void RenameByHandle(SafeFileHandle handle, string destinationPath)
    {
        string destination = NormalizePath(destinationPath);
        if (destination.StartsWith(@"\\", StringComparison.Ordinal))
        {
            throw new ArgumentException("NEWS_GRASP_ATOMIC_UNC_DESTINATION_FORBIDDEN");
        }
        string nativeDestination = @"\??\" + destination;
        byte[] nameBytes = Encoding.Unicode.GetBytes(nativeDestination);
        int rootOffset = IntPtr.Size;
        int lengthOffset = rootOffset + IntPtr.Size;
        int nameOffset = lengthOffset + sizeof(uint);
        int structureSize = IntPtr.Size == 8 ? 24 : 16;
        int bufferSize = Math.Max(nameOffset + nameBytes.Length, structureSize + nameBytes.Length);
        IntPtr buffer = Marshal.AllocHGlobal(bufferSize);
        try
        {
            for (int index = 0; index < bufferSize; index++)
            {
                Marshal.WriteByte(buffer, index, 0);
            }
            Marshal.WriteByte(buffer, 0, 1);
            Marshal.WriteIntPtr(buffer, rootOffset, IntPtr.Zero);
            Marshal.WriteInt32(buffer, lengthOffset, nameBytes.Length);
            Marshal.Copy(nameBytes, 0, IntPtr.Add(buffer, nameOffset), nameBytes.Length);
            IO_STATUS_BLOCK ioStatus;
            int status = NtSetInformationFile(
                handle,
                out ioStatus,
                buffer,
                (uint)bufferSize,
                FILE_RENAME_INFORMATION_CLASS);
            if (status < 0)
            {
                uint error = RtlNtStatusToDosError(status);
                throw new Win32Exception((int)error, "NEWS_GRASP_ATOMIC_COMMIT_FAILED:" + error.ToString());
            }
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    public static NewsGraspVerifiedFileData ReadVerified(string path, bool requireSingleLink, long maxBytes)
    {
        string parent = Path.GetDirectoryName(NormalizePath(path));
        using (SafeFileHandle parentHandle = OpenVerifiedDirectory(parent))
        using (SafeFileHandle fileHandle = OpenVerifiedFile(
            path,
            GENERIC_READ | FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ,
            true,
            requireSingleLink,
            "NEWS_GRASP_VERIFIED_SOURCE_HARDLINK_FORBIDDEN"))
        using (FileStream stream = new FileStream(fileHandle, FileAccess.Read))
        using (MemoryStream memory = new MemoryStream())
        {
            BY_HANDLE_FILE_INFORMATION before = GetInformation(fileHandle);
            ulong fileSize = ((ulong)before.FileSizeHigh << 32) | before.FileSizeLow;
            if (maxBytes > 0 && fileSize > (ulong)maxBytes)
            {
                throw new IOException("NEWS_GRASP_VERIFIED_FILE_TOO_LARGE");
            }
            stream.CopyTo(memory);
            byte[] bytes = memory.ToArray();
            BY_HANDLE_FILE_INFORMATION after = GetInformation(fileHandle);
            if (requireSingleLink && after.NumberOfLinks != 1)
            {
                throw new IOException("NEWS_GRASP_VERIFIED_SOURCE_HARDLINK_FORBIDDEN");
            }
            return new NewsGraspVerifiedFileData(bytes, HashBytes(bytes), after.NumberOfLinks);
        }
    }

    public static string WriteAtomic(string path, byte[] bytes)
    {
        string destination = NormalizePath(path);
        string parent = Path.GetDirectoryName(destination);
        string temporary = Path.Combine(parent, ".news-grasp-install-" + Guid.NewGuid().ToString("N") + ".tmp");
        bool renamed = false;
        bool verified = false;
        using (SafeFileHandle parentHandle = OpenVerifiedDirectory(parent))
        using (SafeFileHandle temporaryHandle = CreateFileW(
            temporary,
            GENERIC_WRITE | FILE_READ_ATTRIBUTES | DELETE,
            FILE_SHARE_READ,
            IntPtr.Zero,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero))
        {
            if (temporaryHandle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                throw new Win32Exception(error, "NEWS_GRASP_ATOMIC_TEMP_CREATE_FAILED:" + error.ToString());
            }
            try
            {
                uint written;
                if (!WriteFile(temporaryHandle, bytes, (uint)bytes.Length, out written, IntPtr.Zero) || written != bytes.Length)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "NEWS_GRASP_ATOMIC_TEMP_WRITE_FAILED");
                }
                if (!FlushFileBuffers(temporaryHandle))
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "NEWS_GRASP_ATOMIC_TEMP_FLUSH_FAILED");
                }
                BY_HANDLE_FILE_INFORMATION temporaryInformation = GetInformation(temporaryHandle);
                if (temporaryInformation.NumberOfLinks != 1 ||
                    (temporaryInformation.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 ||
                    !String.Equals(GetFinalPath(temporaryHandle), NormalizePath(temporary), StringComparison.OrdinalIgnoreCase))
                {
                    throw new IOException("NEWS_GRASP_ATOMIC_TEMP_IDENTITY_INVALID");
                }
                RenameByHandle(temporaryHandle, destination);
                renamed = true;
                if (!String.Equals(GetFinalPath(temporaryHandle), destination, StringComparison.OrdinalIgnoreCase))
                {
                    throw new IOException("NEWS_GRASP_ATOMIC_POSTCOMMIT_IDENTITY_MISMATCH");
                }
                verified = true;
            }
            finally
            {
                if (!renamed)
                {
                    MarkDelete(temporaryHandle);
                }
            }
        }

        if (!verified)
        {
            throw new IOException("NEWS_GRASP_ATOMIC_POSTCOMMIT_VERIFICATION_FAILED");
        }

        NewsGraspVerifiedFileData installed = ReadVerified(destination, true, 0);
        string expectedHash = HashBytes(bytes);
        if (!String.Equals(installed.Sha256, expectedHash, StringComparison.OrdinalIgnoreCase))
        {
            throw new IOException("NEWS_GRASP_ATOMIC_POSTCOMMIT_HASH_MISMATCH");
        }
        return installed.Sha256;
    }

    public static void DeleteVerified(string path)
    {
        string destination = NormalizePath(path);
        string parent = Path.GetDirectoryName(destination);
        using (SafeFileHandle parentHandle = OpenVerifiedDirectory(parent))
        using (SafeFileHandle fileHandle = OpenVerifiedFile(
            destination,
            DELETE | FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ,
            false,
            false,
            "NEWS_GRASP_VERIFIED_DELETE_HARDLINK_INVALID"))
        {
            MarkDelete(fileHandle);
        }
    }
}
'@
}
