# Run in PowerShell 7 on a Windows desktop without an existing Calender installation.
param([Parameter(Mandatory)][string]$Installer)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$setup = (Resolve-Path -LiteralPath $Installer).Path
$version = (Get-Item -LiteralPath $setup).VersionInfo.ProductVersion
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E1509736-CE1A-42CA-AD11-F9F885ECD5B4}_is1'
$startLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Calender.lnk'
$desktopLink = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Calender.lnk'
foreach ($path in @($uninstallKey, $startLink, $desktopLink)) {
    if (Test-Path -LiteralPath $path) { throw "Existing installation or shortcut at $path. Use a clean test account." }
}
$testRoot = [IO.Directory]::CreateTempSubdirectory('Calender-installer-').FullName
$installDir = Join-Path $testRoot 'Program Files/Calender'
$profileDir = Join-Path $testRoot 'Profile'
$exe = Join-Path $installDir 'Calender.exe'
$uninstaller = Join-Path $installDir 'unins000.exe'
$database = Join-Path $profileDir 'SchoolCalendar/calendar.sqlite3'
$sample = 'Installer verification entry'
$shellLinks = New-Object -ComObject WScript.Shell

function Run-Setup([string]$program, [string[]]$options) {
    $info = [Diagnostics.ProcessStartInfo]::new($program)
    $info.UseShellExecute = $false
    foreach ($item in (@('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-') + $options)) {
        $info.ArgumentList.Add($item)
    }
    $p = [Diagnostics.Process]::Start($info)
    if (!$p.WaitForExit(120000) -or $p.ExitCode -ne 0) { throw "Setup failed for $program" }
}

function Check-Link([string]$path) {
    if (!(Test-Path -LiteralPath $path) -or $shellLinks.CreateShortcut($path).TargetPath -ne $exe) {
        throw "Incorrect shortcut at $path"
    }
}

function App-Windows([int]$processId) {
    $match = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $processId)
    [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $match)
}

function Find-Control([int]$processId, [string]$name, $kind) {
    $element = [System.Windows.Automation.AutomationElement]
    $byName = [System.Windows.Automation.PropertyCondition]::new($element::NameProperty, $name)
    $byKind = [System.Windows.Automation.PropertyCondition]::new($element::ControlTypeProperty, $kind)
    $match = [System.Windows.Automation.AndCondition]::new($byName, $byKind)
    for ($i = 0; $i -lt 50; $i++) {
        try {
            foreach ($window in (App-Windows $processId)) {
                $found = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $match)
                if ($found) { return $found }
            }
        } catch { $lastError = $_.Exception.Message } # A dialog can disappear during an accessibility query.
        Start-Sleep -Milliseconds 100
    }
    throw "Missing control '$name' in the installed app. $lastError"
}

function Test-InstalledApp([bool]$createEntry) {
    $info = [Diagnostics.ProcessStartInfo]::new($exe)
    $info.WorkingDirectory = $testRoot
    $info.UseShellExecute = $false
    $info.Environment['LOCALAPPDATA'] = $profileDir
    $info.Environment['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    $null = $info.Environment.Remove('PYTHONPATH')
    $null = $info.Environment.Remove('VIRTUAL_ENV')
    $p = [Diagnostics.Process]::Start($info)
    try {
        $null = $p.WaitForInputIdle(15000)
        for ($i = 0; $i -lt 100 -and !$p.MainWindowHandle; $i++) {
            $p.Refresh()
            Start-Sleep -Milliseconds 100
        }
        if ($p.MainWindowTitle -ne 'Calender') { throw 'Installed app did not display its main window.' }
        $type = [System.Windows.Automation.ControlType]
        $invoke = [System.Windows.Automation.InvokePattern]::Pattern
        if ($createEntry) {
            (Find-Control $p.Id 'Assistant' $type::Button).GetCurrentPattern($invoke).Invoke()
            $null = Find-Control $p.Id 'Send' $type::Button
            $null = Find-Control $p.Id 'Talk' $type::Button
            (Find-Control $p.Id 'Assistant settings' $type::Button).GetCurrentPattern($invoke).Invoke()
            $null = Find-Control $p.Id 'Model routing' $type::ComboBox
            (Find-Control $p.Id 'Save settings' $type::Button).GetCurrentPattern($invoke).Invoke()
            (Find-Control $p.Id 'Add entry' $type::Button).GetCurrentPattern($invoke).Invoke()
            (Find-Control $p.Id 'Title *' $type::Edit).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($sample)
            (Find-Control $p.Id 'Save entry' $type::Button).GetCurrentPattern($invoke).Invoke()
        }
        for ($i = 0; $i -lt 40; $i++) {
            try {
                foreach ($window in (App-Windows $p.Id)) {
                    $children = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                    if ($children | Where-Object { $_.Current.Name -like "$sample*" }) { return }
                }
            } catch { $lastError = $_.Exception.Message }
            Start-Sleep -Milliseconds 100
        }
        throw "The saved entry did not appear in the installed app. $lastError"
    } finally {
        if (!$p.HasExited) {
            $null = $p.CloseMainWindow()
            if (!$p.WaitForExit(4000)) { $p.Kill(); $p.WaitForExit() }
        }
    }
}

try {
    Run-Setup $setup @("/DIR=$installDir")
    $registered = Get-ItemProperty -LiteralPath $uninstallKey
    if ($registered.InstallLocation.TrimEnd('\') -ne $installDir -or $version.Trim() -ne $registered.DisplayVersion) {
        throw 'Incorrect per-user uninstall registration.'
    }
    Check-Link $startLink
    if (Test-Path -LiteralPath $desktopLink) { throw 'Desktop shortcut should be opt-in.' }
    Test-InstalledApp $true
    $before = (Get-FileHash -LiteralPath $database).Hash
    Run-Setup $setup @('/TASKS=desktopicon')
    Check-Link $startLink
    Check-Link $desktopLink
    Test-InstalledApp $false
    Run-Setup $uninstaller @()
    foreach ($path in @($exe, $uninstallKey, $startLink, $desktopLink)) {
        if (Test-Path -LiteralPath $path) { throw "Uninstall left $path" }
    }
    if ((Get-FileHash -LiteralPath $database).Hash -ne $before) { throw 'The saved calendar changed.' }
    'Passed per-user install, optional shortcut, installed app launch and save, reinstall, uninstall, and data preservation.'
} finally {
    if ((Test-Path -LiteralPath $uninstallKey) -and (Test-Path -LiteralPath $uninstaller)) {
        $location = (Get-ItemProperty -LiteralPath $uninstallKey).InstallLocation.TrimEnd('\')
        if ($location -ne $installDir) { throw 'Installation location changed during the test.' }
        Run-Setup $uninstaller @()
    }
    # Inno removes its own executable from a helper process after uninstall returns.
    for ($i = 0; $i -lt 80 -and (Test-Path -LiteralPath $uninstaller); $i++) { Start-Sleep -Milliseconds 100 }
    $resolved = (Resolve-Path -LiteralPath $testRoot).Path
    if ([IO.Path]::GetDirectoryName($resolved) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or
        !([IO.Path]::GetFileName($resolved).StartsWith('Calender-installer-'))) { throw 'Unexpected test directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
