# =====================================================================
# YL_V10.ps1 : YL 자동 메쉬 (2D 스킨) - 설정 창 + hmbatch 실행 + 엔진이 모두 이 파일에 있습니다.
#   실행 : 같은 폴더의 YL_V10.bat 더블클릭 -> 설정 창 -> 모델 선택 -> [실행]
#   필요한 것 : HyperMesh 2019 (hmbatch.exe) + 라이선스
#
#   구성 : 1) 설정 창 + hmbatch 실행 (PowerShell)
#          2) 파일 끝 : HyperMesh 엔진 Tcl 파일들 (실행할 때 %TEMP%\YL_V10_engine 에 풀어서 씀)
#
#   코드 = Problem\V10 스크린샷을 찍은 시점(2026-11-23 21:09)의 V9 엔진, 2D 전용.
#   설정은 %LOCALAPPDATA%\YL_V10\YL_settings.json 에 저장됩니다.
#   hmbatch 가 다른 경로에 있으면 : set YL_HMBATCH=<hmbatch.exe 경로>
#   창 없이 실행 : set YL_JOB_FILE=<job 파일> (선택: set YL_OUT=<출력 폴더>) 후 YL_V10.bat
#       job 파일 한 줄 = 모델 경로 <TAB> 출력 이름 (출력 이름은 비워도 됨)
#   이 파일은 Code\single_bat\build.ps1 로 만듭니다 (직접 고치지 말고 main.ps1 / 엔진을 고친 뒤 다시 빌드).
# =====================================================================
$ErrorActionPreference = 'Continue'
$here  = $PSScriptRoot
$inv   = [Globalization.CultureInfo]::InvariantCulture
$utf8  = New-Object Text.UTF8Encoding $false

# ---------------- engine files ($YLEngineFiles, end of this file) -> temp folder ----------------
function Expand-YLEngine {
    $dir = Join-Path $env:TEMP 'YL_V10_engine'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    foreach ($name in $script:YLEngineFiles.Keys) {
        [IO.File]::WriteAllText((Join-Path $dir $name), $script:YLEngineFiles[$name], $utf8)
    }
    return $dir
}

# ---------------- hmbatch run (was run_hmbatch.ps1) ----------------
function Invoke-YLBatch {
    param(
        [string]$JobFile,
        [double]$ElemSize = 2.0,
        [int]$DoTetra = 0,
        [double]$Aspect = 5.0,
        [string]$ElemType = 'C3D4',
        [int]$ExportInp = 0,
        [string]$OutDir,
        [string]$HmBatch = ''
    )
    $jobLines = @(Get-Content -LiteralPath $JobFile -Encoding UTF8 | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') })
    $all = @($jobLines | ForEach-Object { ($_ -split "`t")[0] })
    if (-not $all) { Write-Error 'job 파일에 모델이 없습니다.'; return 2 }
    if (-not $HmBatch) {
        # the Altair 2019 install has been seen in both layouts
        $hmBatchCandidates = @(
            'C:\Program Files\Altair\2019\hm\bin\win64\hmbatch.exe',
            'C:\Program Files\Altair\2019\2019\hm\bin\win64\hmbatch.exe'
        )
        if ($env:YL_HMBATCH) { $hmBatchCandidates = @($env:YL_HMBATCH) + $hmBatchCandidates }
        $HmBatch = $hmBatchCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $HmBatch) { Write-Error ("hmbatch.exe 를 찾지 못했습니다 (set YL_HMBATCH=경로 로 지정):`n  " + ($hmBatchCandidates -join "`n  ")); return 2 }
    }
    $tclDir = Expand-YLEngine

    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $OutDir = (Resolve-Path -LiteralPath $OutDir).Path
    # everything that is not a meshed model goes below the output folder
    $LogDir = Join-Path $OutDir '_logs'
    $InpDir = Join-Path $OutDir 'inp'
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $runLines = @($jobLines)
    $env:YL_EXPORT_INP = "$ExportInp"
    $env:YL_OUT_DIR   = $OutDir
    $env:YL_LOG_DIR   = $LogDir
    $env:YL_ELEM_SIZE = $ElemSize.ToString($inv)
    $env:YL_DO_TETRA  = "$DoTetra"
    $env:YL_ELEM_TYPE = $ElemType
    $env:YL_ASPECT    = $Aspect.ToString($inv)
    $env:YL_FILLET_ROWS = 'altair'

    $log = Join-Path $LogDir 'batch_log.txt'
    $csv = Join-Path $LogDir 'batch_summary.csv'

    # one hmbatch process over the given model lines; returns the CSV rows
    function Invoke-HmBatchRun([string[]]$lines, [string]$engineVersion, [string]$consoleName) {
        # the list is UTF-8 (run_batch.tcl reads it as UTF-8) so Korean paths work
        $listFile = Join-Path $LogDir '_models.txt'
        [IO.File]::WriteAllLines($listFile, [string[]]@($lines | ForEach-Object { ($_ -split "`t")[0] }), $utf8)
        $env:YL_LIST = $listFile
        $jobRun = Join-Path $LogDir '_job_run.txt'
        [IO.File]::WriteAllLines($jobRun, [string[]]$lines, $utf8)
        $env:YL_JOB = $jobRun
        $env:YL_VERSION = $engineVersion
        # hmbatch writes command.tcl / ct_reader.msg / hmmenu.set into the current folder -> _logs
        Push-Location $LogDir
        try {
            & $HmBatch -tcl (Join-Path $tclDir 'run_batch.tcl') | Out-File -FilePath (Join-Path $LogDir $consoleName) -Encoding utf8
        } finally {
            Pop-Location
        }
        # hmbatch always reports exit code 1 at "End of command file", so the real
        # result is taken from batch_summary.csv (one row per model).
        if (Test-Path -LiteralPath $csv) { return @(Import-Csv -LiteralPath $csv -Encoding UTF8) }
        return @()
    }

    $rows = @(Invoke-HmBatchRun $runLines 'V9' 'hmbatch_console.txt')
    $logText = ''
    if (Test-Path -LiteralPath $log) { $logText = [IO.File]::ReadAllText($log, [Text.Encoding]::UTF8) }

    # models whose BatchMesher skin did not pass (fallback_v8 = 1) are meshed
    # again with the V8 engine in a NEW hmbatch process - the BatchMesher changes
    # session-wide meshing settings, so V8 cannot run in the same session.
    $fallbackRows = @($rows | Where-Object { $_.PSObject.Properties['fallback_v8'] -and "$($_.fallback_v8)".Trim() -eq '1' })
    if ($fallbackRows.Count -gt 0) {
        $names = @($fallbackRows | ForEach-Object { $_.model })
        $fbLines = @($runLines | Where-Object { $names -contains [IO.Path]::GetFileNameWithoutExtension((($_ -split "`t")[0]).Trim().Trim('"')) })
        Write-Host "V8 대체 엔진 : $($fbLines.Count)개 모델 - $($names -join ', ')" -ForegroundColor Yellow
        $header = $null
        if (Test-Path -LiteralPath $csv) { $header = ([IO.File]::ReadAllLines($csv, [Text.Encoding]::UTF8)[0]).TrimStart([char]0xFEFF) }
        $v8Rows = @(Invoke-HmBatchRun $fbLines 'V8' 'hmbatch_console_v8.txt')
        if (Test-Path -LiteralPath $log) { $logText += "`r`n=== V8 fallback (new hmbatch process) ===`r`n" + [IO.File]::ReadAllText($log, [Text.Encoding]::UTF8) }
        $merged = foreach ($r in $rows) {
            $v8 = $v8Rows | Where-Object { $_.model -eq $r.model } | Select-Object -First 1
            if ($names -contains $r.model -and $v8) {
                $v8.engine = 'V9>V8'
                if ($v8.PSObject.Properties['mesher']) { $v8.mesher = 'V8 fallback' }
                if ($v8.PSObject.Properties['fallback_v8']) { $v8.fallback_v8 = '1' }
                $v8
            } else { $r }
        }
        $rows = @($merged)
        $cols = if ($header) { @($header -split ',') } else { @($rows[0].PSObject.Properties.Name) }
        $outLines = @($cols -join ',') + @($rows | Select-Object -Property $cols | ConvertTo-Csv -NoTypeInformation | Select-Object -Skip 1)
        [IO.File]::WriteAllLines($csv, [string[]]$outLines, (New-Object Text.UTF8Encoding $true))
        [IO.File]::WriteAllText($log, $logText, $utf8)
    }

    # tidy the output folder : Abaqus decks -> inp\, files the batch writes -> _logs\
    $generated = @('batch_log.txt', 'batch_summary.csv', 'command.tcl', 'ct_reader.msg', 'hmmenu.set',
                   '_models.txt', '_job.txt', '_job_run.txt')
    Get-ChildItem -LiteralPath $OutDir -File | Where-Object {
        $generated -contains $_.Name -or $_.Name -like 'hmbatch_console*.txt' -or $_.Name -like '_yl[0-9]_*' -or $_.Name -like '*_yl[0-9]_*_res.txt' -or $_.Name -eq 'time_limit.txt'
    } | ForEach-Object { Move-Item -LiteralPath $_.FullName -Destination (Join-Path $LogDir $_.Name) -Force }
    foreach ($r in $rows) {
        $inp = "$($r.inp)".Trim()
        if (-not $inp) { continue }
        $inpPath = $inp -replace '/', '\'
        if ((Test-Path -LiteralPath $inpPath) -and ((Split-Path -Parent $inpPath) -eq $OutDir)) {
            New-Item -ItemType Directory -Force -Path $InpDir | Out-Null
            Move-Item -LiteralPath $inpPath -Destination (Join-Path $InpDir (Split-Path -Leaf $inpPath)) -Force
        }
    }

    if ($logText) { $logText -split "`r?`n" | Write-Host }
    Write-Host "결과 : $OutDir (*.hm)   로그 / 요약 : $LogDir" -ForegroundColor Cyan
    Write-Host ''
    $rows | Format-Table model, engine, mesher, status, pass2d, n_2d, bad_aspect_2d, free_edges, t_edges, q_mean, seconds -AutoSize | Out-String -Width 250 | Write-Host
    if ($rows.Count -lt $all.Count) {
        Write-Warning "$($all.Count)개 중 $($rows.Count)개만 처리됨 - hmbatch 가 중간에 멈췄습니다 (_logs\hmbatch_console.txt, _logs\batch_log.txt 확인)"
        return 1
    }
    $fail = @($rows | Where-Object { $_.status -ne 'OK' }).Count
    if ($fail -gt 0) { Write-Warning "$fail 개 모델 실패"; return 1 }
    return 0
}

# ---------------- main : settings window -> run (called at the very end of the file) ----------------
function Start-YL {
# settings (last used values)
$cfgDir       = Join-Path $env:LOCALAPPDATA 'YL_V10'
$settingsFile = Join-Path $cfgDir 'YL_settings.json'
$cfg = @{
    ElemSize = 2.0; Aspect = 5.0
    NameMode = 'suffix'; Suffix = '_MODEL'; AddInfo = $false
    OutDir = (Join-Path $here 'batch_out'); LastFolder = $here
}
if (Test-Path -LiteralPath $settingsFile) {
    try {
        $saved = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($k in @($cfg.Keys)) { if ($null -ne $saved.$k) { $cfg[$k] = $saved.$k } }
    } catch { }
}

# ---------------- no window : YL_JOB_FILE ----------------
if ($env:YL_JOB_FILE) {
    $outDir = if ($env:YL_OUT) { $env:YL_OUT } else { [string]$cfg.OutDir }
    return (Invoke-YLBatch -JobFile $env:YL_JOB_FILE -ElemSize ([double]$cfg.ElemSize) -Aspect ([double]$cfg.Aspect) -OutDir $outDir)
}

# ---------------- window (was YL_setup.ps1) ----------------
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()

$font = New-Object Drawing.Font('Malgun Gothic', 9)
$form = New-Object Windows.Forms.Form
$form.Text = 'YL Mesh V10 (2D 스킨)'
$form.Font = $font
$form.ClientSize = New-Object Drawing.Size(860, 520)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

function New-Ctl($type, $text, $x, $y, $w, $h, $parent) {
    $c = New-Object "Windows.Forms.$type"
    if ($null -ne $text) { $c.Text = $text }
    $c.Location = New-Object Drawing.Point($x, $y)
    $c.Size = New-Object Drawing.Size($w, $h)
    $parent.Controls.Add($c)
    return $c
}

# ① models
$gbModels = New-Ctl GroupBox '① 모델' 12 8 836 280 $form
$btnFiles  = New-Ctl Button '파일 추가...'  12 22 100 26 $gbModels
$btnFolder = New-Ctl Button '폴더 추가...' 118 22 100 26 $gbModels
$btnRemove = New-Ctl Button '선택 제거'    224 22 100 26 $gbModels
$btnClear  = New-Ctl Button '모두 제거'    330 22 100 26 $gbModels
$lblCount  = New-Ctl Label  ''             440 27 380 20 $gbModels

$grid = New-Object Windows.Forms.DataGridView
$grid.Location = New-Object Drawing.Point(12, 54)
$grid.Size = New-Object Drawing.Size(812, 216)
$grid.AllowUserToAddRows = $false
$grid.AllowUserToResizeRows = $false
$grid.RowHeadersVisible = $false
$grid.SelectionMode = 'FullRowSelect'
$grid.EditMode = 'EditOnEnter'
$grid.BackgroundColor = [Drawing.SystemColors]::Window
$colPath = New-Object Windows.Forms.DataGridViewTextBoxColumn
$colPath.Name = 'Path'; $colPath.Visible = $false
$colSrc = New-Object Windows.Forms.DataGridViewTextBoxColumn
$colSrc.Name = 'Src'; $colSrc.HeaderText = '원본 모델'; $colSrc.ReadOnly = $true; $colSrc.Width = 380
$colOut = New-Object Windows.Forms.DataGridViewTextBoxColumn
$colOut.Name = 'Out'; $colOut.HeaderText = '출력 이름 (.hm)'; $colOut.Width = 410
[void]$grid.Columns.AddRange($colPath, $colSrc, $colOut)
$gbModels.Controls.Add($grid)

# ② mesh
$gbMesh = New-Ctl GroupBox '② 메시 (2D 스킨만 만듭니다)' 12 294 836 60 $form
[void](New-Ctl Label '요소 크기 (mm)' 14 27 94 20 $gbMesh)
$numSize = New-Ctl NumericUpDown $null 108 24 70 24 $gbMesh
$numSize.DecimalPlaces = 2; $numSize.Minimum = 0.1; $numSize.Maximum = 100; $numSize.Increment = 0.5
[void](New-Ctl Label 'Aspect ratio 한계' 210 27 105 20 $gbMesh)
$numAsp = New-Ctl NumericUpDown $null 318 24 70 24 $gbMesh
$numAsp.DecimalPlaces = 1; $numAsp.Minimum = 1.5; $numAsp.Maximum = 100; $numAsp.Increment = 0.5

# ③ naming
$gbName = New-Ctl GroupBox '③ 출력 모델 이름' 12 360 836 90 $form
$rbNameSame   = New-Ctl RadioButton '원래 이름 그대로' 14 22 140 22 $gbName
$rbNameSuffix = New-Ctl RadioButton '원래 이름 + 접미사' 14 44 140 22 $gbName
$txtSuffix    = New-Ctl TextBox $null 158 43 130 24 $gbName
$rbNameCustom = New-Ctl RadioButton '모델별로 직접 입력 (위 표의 "출력 이름" 열을 고침)' 14 66 360 22 $gbName
$chkInfo      = New-Ctl CheckBox '메시 정보 붙이기 (예: _2D_2.0mm)' 420 22 300 22 $gbName

# ④ output
$gbOut = New-Ctl GroupBox '④ 출력 폴더' 12 456 620 54 $form
$txtOut    = New-Ctl TextBox $null 14 22 520 24 $gbOut
$btnBrowse = New-Ctl Button '찾기...' 540 21 70 26 $gbOut

$btnRun    = New-Ctl Button '실행' 652 472 95 30 $form
$btnCancel = New-Ctl Button '취소' 753 472 95 30 $form
$form.CancelButton = $btnCancel
$btnRun.Font = New-Object Drawing.Font('Malgun Gothic', 9, [Drawing.FontStyle]::Bold)

# ---------------- initial values ----------------
$numSize.Value = [decimal][Math]::Min(100, [Math]::Max(0.1, [double]$cfg.ElemSize))
$numAsp.Value  = [decimal][Math]::Min(100, [Math]::Max(1.5, [double]$cfg.Aspect))
switch ($cfg.NameMode) { 'same' { $rbNameSame.Checked = $true } 'custom' { $rbNameCustom.Checked = $true } default { $rbNameSuffix.Checked = $true } }
$txtSuffix.Text  = [string]$cfg.Suffix
$chkInfo.Checked = [bool]$cfg.AddInfo
$txtOut.Text     = [string]$cfg.OutDir

# ---------------- behaviour ----------------
$script:busy = $false
$script:result = $null

function Get-AutoName($row) {
    $name = [IO.Path]::GetFileNameWithoutExtension([string]$row.Cells['Path'].Value)
    if ($rbNameSuffix.Checked) { $name += $txtSuffix.Text.Trim() }
    if ($chkInfo.Checked) { $name += '_2D_' + ([double]$numSize.Value).ToString('0.0##', $inv) + 'mm' }
    return $name
}

function Update-View {
    if ($script:busy) { return }
    $script:busy = $true
    try {
        $txtSuffix.Enabled = $rbNameSuffix.Checked
        $chkInfo.Enabled = -not $rbNameCustom.Checked
        $grid.Columns['Out'].ReadOnly = -not $rbNameCustom.Checked
        $grid.Columns['Out'].DefaultCellStyle.BackColor = if ($grid.Columns['Out'].ReadOnly) { [Drawing.SystemColors]::Control } else { [Drawing.SystemColors]::Window }
        foreach ($row in $grid.Rows) {
            if (-not $rbNameCustom.Checked -or -not $row.Cells['Out'].Value) { $row.Cells['Out'].Value = Get-AutoName $row }
            $row.Cells['Out'].Style.BackColor = [Drawing.Color]::Empty
        }
        $lblCount.Text = "모델 $($grid.Rows.Count)개"
    } finally { $script:busy = $false }
}

function Add-Models([string[]]$paths) {
    $have = @{}
    foreach ($row in $grid.Rows) { $have[([string]$row.Cells['Path'].Value).ToLower()] = 1 }
    foreach ($p in $paths) {
        if (-not $p -or $have.ContainsKey($p.ToLower())) { continue }
        $have[$p.ToLower()] = 1
        $i = $grid.Rows.Add()
        $grid.Rows[$i].Cells['Path'].Value = $p
        $grid.Rows[$i].Cells['Src'].Value  = [IO.Path]::GetFileName($p)
        $grid.Rows[$i].Cells['Src'].ToolTipText = $p
    }
    Update-View
}

# returns $true when every output name is usable; marks the bad rows
function Test-Names {
    $bad = @{}; $seen = @{}
    $invalid = [IO.Path]::GetInvalidFileNameChars()
    foreach ($row in $grid.Rows) {
        $n = ([string]$row.Cells['Out'].Value).Trim()
        $row.Cells['Out'].Value = $n
        if (-not $n -or $n.IndexOfAny($invalid) -ge 0) { $bad[$row.Index] = 1 }
        $k = $n.ToLower()
        if ($seen.ContainsKey($k)) { $bad[$row.Index] = 1; $bad[$seen[$k]] = 1 } else { $seen[$k] = $row.Index }
    }
    foreach ($row in $grid.Rows) {
        $row.Cells['Out'].Style.BackColor = if ($bad.ContainsKey($row.Index)) { [Drawing.Color]::MistyRose } else { [Drawing.Color]::Empty }
    }
    $script:nameProblems = $bad.Count
    return ($bad.Count -eq 0)
}

function Invoke-Run {
    [void]$grid.EndEdit()
    if ($grid.Rows.Count -eq 0) { [void][Windows.Forms.MessageBox]::Show('모델을 먼저 추가하세요.', $form.Text); return }
    if (-not $txtOut.Text.Trim()) { [void][Windows.Forms.MessageBox]::Show('출력 폴더를 지정하세요.', $form.Text); return }
    if (-not (Test-Names)) {
        [void][Windows.Forms.MessageBox]::Show("출력 이름을 확인하세요 (붉게 표시된 $($script:nameProblems)개 행).`n같은 이름이 두 번 나오거나 파일 이름에 쓸 수 없는 문자가 있습니다.", $form.Text)
        return
    }
    $outDir = $txtOut.Text.Trim()
    $exists = @()
    foreach ($row in $grid.Rows) {
        $f = Join-Path $outDir (([string]$row.Cells['Out'].Value) + '.hm')
        if (Test-Path -LiteralPath $f) { $exists += [IO.Path]::GetFileName($f) }
    }
    if ($exists.Count -gt 0) {
        $shown = ($exists | Select-Object -First 8) -join "`n"
        if ($exists.Count -gt 8) { $shown += "`n... 외 $($exists.Count - 8)개" }
        $ans = [Windows.Forms.MessageBox]::Show("출력 폴더에 같은 이름의 파일이 이미 있습니다. 덮어쓸까요?`n`n$shown", $form.Text, 'YesNo', 'Warning')
        if ($ans -ne 'Yes') { return }
    }
    $jobRows = @()
    foreach ($row in $grid.Rows) { $jobRows += ([string]$row.Cells['Path'].Value) + "`t" + ([string]$row.Cells['Out'].Value) + "`t" }
    $script:result = @{ Job = $jobRows; OutDir = $outDir; ElemSize = [double]$numSize.Value; Aspect = [double]$numAsp.Value }
    $cfg.ElemSize = [double]$numSize.Value; $cfg.Aspect = [double]$numAsp.Value
    $cfg.NameMode = if ($rbNameSame.Checked) { 'same' } elseif ($rbNameCustom.Checked) { 'custom' } else { 'suffix' }
    $cfg.Suffix = $txtSuffix.Text.Trim(); $cfg.AddInfo = [bool]$chkInfo.Checked
    $cfg.OutDir = $outDir
    try {
        New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
        [IO.File]::WriteAllText($settingsFile, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $true))
    } catch { }
    $form.DialogResult = 'OK'
    $form.Close()
}

$btnFiles.Add_Click({
    $d = New-Object Windows.Forms.OpenFileDialog
    $d.Multiselect = $true
    $d.Filter = 'CAD / HyperMesh (*.stp;*.step;*.igs;*.iges;*.hm)|*.stp;*.step;*.igs;*.iges;*.hm|모든 파일 (*.*)|*.*'
    if ($cfg.LastFolder -and (Test-Path -LiteralPath $cfg.LastFolder)) { $d.InitialDirectory = $cfg.LastFolder }
    if ($d.ShowDialog($form) -eq 'OK') {
        $cfg.LastFolder = [IO.Path]::GetDirectoryName($d.FileNames[0])
        Add-Models $d.FileNames
    }
})
$btnFolder.Add_Click({
    $d = New-Object Windows.Forms.FolderBrowserDialog
    $d.Description = '이 폴더와 하위 폴더의 STP 파일을 모두 추가합니다.'
    if ($cfg.LastFolder -and (Test-Path -LiteralPath $cfg.LastFolder)) { $d.SelectedPath = $cfg.LastFolder }
    if ($d.ShowDialog($form) -eq 'OK') {
        $cfg.LastFolder = $d.SelectedPath
        $found = @(Get-ChildItem -LiteralPath $d.SelectedPath -Recurse -File | Where-Object { $_.Extension -match '^\.(stp|step)$' } | ForEach-Object { $_.FullName })
        if ($found.Count -eq 0) { [void][Windows.Forms.MessageBox]::Show('STP 파일이 없습니다.', $form.Text) } else { Add-Models $found }
    }
})
$btnRemove.Add_Click({
    foreach ($r in @($grid.SelectedRows)) { $grid.Rows.Remove($r) }
    Update-View
})
$btnClear.Add_Click({ $grid.Rows.Clear(); Update-View })
$btnBrowse.Add_Click({
    $d = New-Object Windows.Forms.FolderBrowserDialog
    if (Test-Path -LiteralPath $txtOut.Text) { $d.SelectedPath = $txtOut.Text }
    if ($d.ShowDialog($form) -eq 'OK') { $txtOut.Text = $d.SelectedPath }
})
foreach ($c in @($rbNameSame, $rbNameSuffix, $rbNameCustom)) { $c.Add_CheckedChanged({ Update-View }) }
$chkInfo.Add_CheckedChanged({ Update-View })
$txtSuffix.Add_TextChanged({ Update-View })
$numSize.Add_ValueChanged({ Update-View })
$grid.Add_DataError({ param($s, $e) $e.ThrowException = $false })
$btnRun.Add_Click({ Invoke-Run })
$btnCancel.Add_Click({ $form.Close() })

Update-View
[void]$form.ShowDialog()
if (-not $script:result) { Write-Host '취소했습니다.'; return 0 }

$r = $script:result
New-Item -ItemType Directory -Force -Path (Join-Path $r.OutDir '_logs') | Out-Null
$jobFile = Join-Path $r.OutDir '_logs\_job.txt'
[IO.File]::WriteAllLines($jobFile, [string[]]$r.Job, $utf8)
Write-Host "모델 $($r.Job.Count)개를 hmbatch로 처리합니다. 창을 닫지 마세요..."
$code = Invoke-YLBatch -JobFile $jobFile -ElemSize $r.ElemSize -Aspect $r.Aspect -OutDir $r.OutDir
Write-Host ''
Write-Host "결과 폴더: $($r.OutDir)  (*.hm,  _logs\batch_summary.csv / batch_log.txt)"
return $code
}

# =====================================================================
# HyperMesh engine files (Tcl) - written to %TEMP%\YL_V10_engine when a run starts
# =====================================================================
$YLEngineFiles = [ordered]@{}

# ----- run_batch.tcl -----
$YLEngineFiles['run_batch.tcl'] = @'
# =====================================================================
# run_batch.tcl : hmbatch driver for the YL_V<n>_batch.tcl engines
#   hmbatch.exe -tcl run_batch.tcl
# Settings come from environment variables (set by run_hmbatch.ps1):
#   YL_VERSION    engine version, e.g. V8 / V9 / 9     default : highest YL_V*_batch.tcl
#   YL_LIST       text file, one model path per line (.stp/.step/.igs/.hm ...)
#   YL_OUT_DIR    output folder (.hm / .inp results)
#   YL_LOG_DIR    folder for batch_log.txt / batch_summary.csv   default : YL_OUT_DIR
#   YL_ELEM_SIZE  target element size (mm)          default 2.0
#   YL_DO_TETRA   0 = 2D only, 1 = 2D + tetra (tetra only when 2D passes)   default 0
#   YL_ELEM_TYPE  C3D4 / C3D4H / C3D10H             default C3D10H
#   YL_ASPECT     2D aspect ratio limit             default 5.0
#   YL_JOB        TAB separated job file (path, output name, element type)
#   YL_EXPORT_INP 3D mode: also write an Abaqus .inp (1/0)   default 0
# =====================================================================
set YLB_HERE   [file dirname [file normalize [info script]]]

proc ylb_env {name default} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $default
}

# engine : YL_VERSION (V8 / 8) or the highest numbered YL_V<n>_batch.tcl.
# Every change of the meshing logic is a new YL_V<n+1> file, older ones stay
# runnable so that their results can be compared.
proc ylb_latest_version {dir} {
    set best -1
    foreach f [glob -nocomplain -directory $dir "YL_V*_batch.tcl"] {
        if {[regexp {^YL_V(\d+)_batch\.tcl$} [file tail $f] -> n] && $n > $best} { set best $n }
    }
    return $best
}
set YLB_VERSION [string toupper [string trim [ylb_env YL_VERSION ""]]]
if {$YLB_VERSION eq ""} {
    set YLB_VERSION "V[ylb_latest_version $YLB_HERE]"
} elseif {[string index $YLB_VERSION 0] ne "V"} {
    set YLB_VERSION "V$YLB_VERSION"
}
set YLB_ENGINE [file join $YLB_HERE "YL_${YLB_VERSION}_batch.tcl"]

set YLB_LIST    [ylb_env YL_LIST    [file join $YLB_HERE "models.txt"]]
set YLB_OUT_DIR [file normalize [ylb_env YL_OUT_DIR [file join $YLB_HERE "batch_out"]]]
file mkdir $YLB_OUT_DIR
set YLB_LOG_DIR [file normalize [ylb_env YL_LOG_DIR $YLB_OUT_DIR]]
file mkdir $YLB_LOG_DIR

# list / log / csv are UTF-8 so that Korean file names survive
set YLB_LOG [open [file join $YLB_LOG_DIR "batch_log.txt"] w]
fconfigure $YLB_LOG -encoding utf-8
proc ylb_log {txt} {
    puts $::YLB_LOG $txt
    flush $::YLB_LOG
    # NOTE: no "puts stdout" here - hmbatch has no usable stdout and the text
    #       ends up inside whichever file was opened next (the CSV).
}

proc ylb_reset_model {} {
    catch { hm_answernext yes }
    if {[catch { *deletemodel } err]} {
        ylb_log "  WARN: *deletemodel failed: $err"
    }
}

proc ylb_load_model {path} {
    set ext [string tolower [file extension $path]]
    if {$ext eq ".hm"} {
        catch { hm_answernext yes }
        *readfile $path
    } else {
        # same options HyperMesh records for File > Import > Geometry
        *geomimport "auto_detect" $path "CleanupTol=-0.01" "CreationType=Parts" \
            "DoNotMergeEdges=off" "ImportBlanked=off" "SplitComponents=Part" \
            "TargetUnits=CAD units"
    }
}

proc ylb_count {type} {
    *clearmark $type 1
    *createmark $type 1 "all"
    set n [llength [hm_getmark $type 1]]
    *clearmark $type 1
    return $n
}

# ---------------- read model list ----------------
# YL_JOB (written by YL_setup.ps1) : one model per line, TAB separated
#     model path <TAB> output name (no extension) <TAB> element type
# empty 2nd / 3rd fields fall back to the automatic name / YL_ELEM_TYPE.
# Without YL_JOB the plain list YL_LIST is used (path only).
set YLB_JOB [ylb_env YL_JOB ""]
if {$YLB_JOB ne ""} { set YLB_LIST $YLB_JOB }
set YLB_MODELS [list]
array set YLB_OUTNAME {}
array set YLB_ETYPE {}
if {[catch {open $YLB_LIST r} fh]} {
    ylb_log "ERROR: cannot open model list: $YLB_LIST"
} else {
    fconfigure $fh -encoding utf-8
    foreach line [split [read $fh] "\n"] {
        set line [string trim [string trimleft $line "﻿"] " \r\n"]
        if {[string trim $line] eq "" || [string index $line 0] eq "#"} { continue }
        set f [split $line "\t"]
        set p [file normalize [string trim [string trim [lindex $f 0]] "\""]]
        lappend YLB_MODELS $p
        set YLB_OUTNAME([llength $YLB_MODELS]) [string trim [lindex $f 1]]
        set YLB_ETYPE([llength $YLB_MODELS])   [string trim [lindex $f 2]]
    }
    close $fh
}

ylb_log "YL batch start : [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
ylb_log "  engine  : $YLB_VERSION ($YLB_ENGINE)"
if {![file exists $YLB_ENGINE]} { ylb_log "ERROR: engine not found: $YLB_ENGINE" }
ylb_log "  out dir : $YLB_OUT_DIR"
ylb_log "  models  : [llength $YLB_MODELS]"

set YLB_CSV [open [file join $YLB_LOG_DIR "batch_summary.csv"] w]
fconfigure $YLB_CSV -encoding utf-8
puts -nonewline $YLB_CSV "﻿"   ;# BOM : Excel then shows Korean names correctly
puts $YLB_CSV "model,status,elem_size,surfs,n_2d_raw,bad_aspect_raw,n_2d,bad_aspect_2d,free_edges,t_edges,n_tet,bad_tet,collapses,flips,splits,seconds,output,note,elem_type,inp,pass2d,defeatured,edge_pairs,edge_mismatch,curv_coarse,curv_manual,curv_manual_where,avg_size,engine,fillet_max_nodes,band_unpaired,mesher,q_mean,ang_lt20,asp_gt3,tet_asp_p95,tet_asp_gt5,tet_col_lt015,tet_dih_lt10,fallback_v8,trace_slivers,trace_vertices,bands"

set YLB_NOK 0
set YLB_NFAIL 0
set YLB_IDX 0

foreach YLB_MODEL $YLB_MODELS {
    incr YLB_IDX
    set t0 [clock seconds]
    set name [file rootname [file tail $YLB_MODEL]]
    ylb_log ""
    ylb_log "\[$YLB_IDX/[llength $YLB_MODELS]\] $YLB_MODEL"

    set ::YL_BATCH     1
    set ::YL_ELEM_SIZE [ylb_env YL_ELEM_SIZE 2.0]
    set ::YL_DO_TETRA  [ylb_env YL_DO_TETRA 0]
    set ::YL_ELEM_TYPE [ylb_env YL_ELEM_TYPE "C3D10H"]
    set ::YL_ASPECT    [ylb_env YL_ASPECT 5.0]
    set ::YL_OUT_DIR   $YLB_OUT_DIR
    set ::YL_LOG_DIR   $YLB_LOG_DIR
    set ::YL_BASE_NAME $name
    set ::YL_EXPORT_INP [ylb_env YL_EXPORT_INP 0]
    set ::YL_OUT_NAME  $YLB_OUTNAME($YLB_IDX)
    # V9+ : 0 = no V8 fallback when the BatchMesher skin fails (troubleshooting)
    if {[ylb_env YL_V8_FALLBACK ""] ne ""} { set ::YL_V8_FALLBACK [ylb_env YL_V8_FALLBACK 1] }
    if {$YLB_ETYPE($YLB_IDX) ne ""} { set ::YL_ELEM_TYPE $YLB_ETYPE($YLB_IDX) }
    set ::YL_RESULT   [list status "FAIL" msg "engine did not run"]

    set note ""
    if {![file exists $YLB_MODEL]} {
        set ::YL_RESULT [list status "FAIL" msg "file not found"]
    } else {
        ylb_reset_model
        if {[catch { ylb_load_model $YLB_MODEL } err]} {
            set ::YL_RESULT [list status "FAIL" msg "import failed: $err"]
        } else {
            ylb_log "  imported: [ylb_count surfs] surfs, [ylb_count solids] solids, [ylb_count comps] comps"
            if {[catch { uplevel #0 [list source $YLB_ENGINE] } err]} {
                set ::YL_RESULT [list status "FAIL" msg "script error: $err"]
                ylb_log "  --- errorInfo ---\n$::errorInfo\n  -----------------"
            }
        }
    }

    array unset R
    array set R {status FAIL msg "" out "" size "" surfs "" n2d_raw "" bad2d_raw "" n2d "" bad2d "" free_edges "" t_edges "" ntet "" badtet "" collapse "" flip "" split "" etype "" inp "" pass2d "" defeatured "" edge_pairs "" edge_mismatch "" curv_coarse "" curv_manual "" curv_manual_where "" avg_size "" engine "" fillet_max_nodes "" band_unpaired "" mesher "" q_mean "" ang_lt20 "" asp_gt3 "" tet_asp_p95 "" tet_asp_gt5 "" tet_col_lt015 "" tet_dih_lt10 "" fallback_v8 "" trace_slivers "" trace_vertices "" bands ""}
    set R(engine) $YLB_VERSION
    array set R $::YL_RESULT
    set dt [expr {[clock seconds] - $t0}]
    if {$R(status) eq "OK"} { incr YLB_NOK } else { incr YLB_NFAIL }

    ylb_log "  => $R(status)  (${dt}s)  2D pass: [expr {$R(pass2d) eq "" ? "?" : ($R(pass2d) ? "yes" : "no")}]"
    foreach ln [split $R(msg) "\n"] { if {$ln ne ""} { ylb_log "     $ln" } }

    set note [string map [list "\n" " | " "," ";" "\"" "'"] [expr {$R(status) eq "OK" ? "" : $R(msg)}]]
    puts $YLB_CSV "\"$name\",$R(status),$R(size),$R(surfs),$R(n2d_raw),$R(bad2d_raw),$R(n2d),$R(bad2d),$R(free_edges),$R(t_edges),$R(ntet),$R(badtet),$R(collapse),$R(flip),$R(split),$dt,\"$R(out)\",\"$note\",$R(etype),\"$R(inp)\",$R(pass2d),$R(defeatured),$R(edge_pairs),$R(edge_mismatch),$R(curv_coarse),$R(curv_manual),\"[string map [list \" '] $R(curv_manual_where)]\",$R(avg_size),$R(engine),$R(fillet_max_nodes),$R(band_unpaired),$R(mesher),$R(q_mean),$R(ang_lt20),$R(asp_gt3),$R(tet_asp_p95),$R(tet_asp_gt5),$R(tet_col_lt015),$R(tet_dih_lt10),$R(fallback_v8),$R(trace_slivers),$R(trace_vertices),$R(bands)"
    flush $YLB_CSV
}

ylb_log ""
ylb_log "DONE : OK=$YLB_NOK  FAIL=$YLB_NFAIL  ([clock format [clock seconds] -format {%H:%M:%S}])"
close $YLB_CSV
close $YLB_LOG
exit [expr {$YLB_NFAIL > 0 ? 1 : 0}]
'@

# ----- YL_V9_batch.tcl -----
$YLEngineFiles['YL_V9_batch.tcl'] = @'
# =====================================================================
# YL_V9 : 2D ONLY (the tetra mesh is made by the analyst) -
#         HyperMesh BatchMesher skin (*hm_batchmesh, QI criteria + parameter
#         file) -> repair -> 3D tetra only when the 2D skin has passed.
#         Models whose BatchMesher skin does not pass go to the V8 engine.
#
# Why (comparison hand mesh <-> V8, 2026-11-23, YL_mesh_stats.tcl on 5 hand
# meshed models and V8 run on the same STEP files):
#   - V8 fixes every division count itself (fillet rule, band matching) and then
#     repairs the aspect with collapses. Result: faceted fillets (1 element over
#     90 deg), mesh lines fixed on every CAD edge, per-face grid directions,
#     abrupt size jumps; skin trias with aspect > 3 : 3-8 %, obtuse : 4-6 %.
#   - The hand meshes are smooth : size transitions <= 1.3-1.4, fillets with
#     rows, tangent edges are not visible in the mesh.
#   - HyperMesh's own BatchMesher (the automesh Altair ships for production
#     meshing, also run by hw_batchmesh) does what the analyst does by hand:
#     geometry clean-up (tangent / short edges suppressed, narrow surfaces
#     merged), fillet recognition with a row table, QI-optimised meshing
#     against a criteria file, minimised transitions. Measured on the same
#     5 geometries + tetmesh: tetra aspect p95 4.6-5.3 (hand 4.6-6.7, V8 5.5-6.2),
#     tet collapse < 0.15 : 1.2-1.6 % (hand 1.0-1.9 %, V8 1.5-2.0 %),
#     skin q mean 0.90 (hand 0.75-0.86, V8 0.79-0.82).
#
# Steps
#   1. Criteria / parameter files are written for the target size. They are
#      Altair's tetra_5mm template (hm/batchmesh) scaled to ELEM_SIZE:
#      min length fail 0.25 x size (hand meshes go down to ~0.25 x size on
#      fillets), max length fail 1.8 x size, aspect fail 5, tria angles 20/130,
#      feature angle 30 (Altair guideline 20-30), fillet row table
#      (1 / 2 / 3 rows by fillet width, chordal deviation for wider ones,
#      minimize transitions on), trias only (element_type 0, best tetra skin).
#   2. *hm_batchmesh on all surfaces (lines masked, *readqualitycriteria first:
#      required in batch mode, same as hm/scripts/batchmesh/hmbm.tcl).
#   3. Repair : zero volume double faces removed, YL_V8_qclean (collapse / flip /
#      split) for anything above the aspect limit.
#   4. Check : aspect (HyperMesh default definition), free edges, T-edges,
#      unmeshed surfaces -> pass2d. Not passed -> fallback_v8 = 1 :
#      run_hmbatch.ps1 re-runs the model with the V8 engine in a new hmbatch
#      process (engine "V9>V8"). GUI : the message says to run YL_V8 by hand.
#   5. 3D : DO_TETRA = 1 and pass2d = 1 -> *tetmesh + Abaqus element type
#      (same as V8), mesh quality report (YL_mesh_stats.tcl procs).
#
# Batch : run_batch.tcl sets ::YL_BATCH 1 and the ::YL_* values.
# GUI   : "run" this file directly -> asks for the size and the surfaces.
# =====================================================================
if {![info exists ::YL_BATCH]} { set ::YL_BATCH 0 }
set ::YL_RESULT [list status "FAIL" msg "not finished"]

# ---------------- settings ----------------
set DO_TETRA          0
set ABAQUS_ELEM_TYPE  "C3D10H"
set ELEM_SIZE         2.0
set TARGET_ASPECT     5.0
set FEATURE_ANGLE     30.0       ;# BatchMesher feature angle (Altair: 20-30)
set CLEANUP_ANGLE     25.0       ;# qclean : edges sharper than this are feature lines
set MIN_LEN_FACTOR    0.25       ;# x size : criteria "min length" fail value
set MAX_LEN_FACTOR    1.8        ;# x size : criteria "max length" fail value
# trace clean-up (step 0) : slivers left by features cut off in CAD
set TRACE_AREA        0.025      ;# x size^2 : sliver surface area below this ...
set TRACE_WIDTH       0.025      ;# x size   : ... and width (area / longest edge) below this
set TRACE_ZONE        1.0        ;# x size   : vertices this close to a sliver are checked
set TRACE_KEEP_LEN    0.25       ;# x size   : vertex between two edges longer than this ...
set TRACE_KINK        30.0       ;# deg      : ... that turn more than this is a real corner (kept)
# curved narrow bands (step 3b) : structured mesh
set BAND_MIN_TURN     120.0      ;# deg : both short sides of the band are arcs turning at least this (rounded rib tip)
set BAND_MIN_RATIO    4.0        ;# band length / width at least this (narrow strip)
set BAND_TRIGGER_DEG  40.0       ;# deg : a structured band is remeshed only when it has more turn than this per element
set BAND_ARC_DEG      35.0       ;# deg : one element per this much turn across the band
set BAND_MAX_ASPECT   4.0        ;# longest edge / shortest edge of the band cells
set BAND_RELAX        2.0        ;# x limit : neighbour trias may get this bad while nodes are inserted (repaired by the clean-up)
set FILLET_ROWS       "altair"   ;# altair : tetra_5mm row table scaled / dense : 2-4 rows also on small fillets (hand style)
set DELETE_2D_SKIN    1
set TET_GROWTH        1.2
set MIN_TET_COLLAPSE  0.15
set V8_FALLBACK       1          ;# 1 : skin not passed -> flagged, run_hmbatch.ps1 re-runs it with V8 (new process)
set OUT_DIR           "C:/work/mesh"
set BASE_NAME         "YL_V9"

if {[info exists ::YL_DO_TETRA]}  { set DO_TETRA $::YL_DO_TETRA }
# 2026-11-23 : the analyst meshes the 3D (tetra) himself - V9 writes the 2D
# skin only, whatever DoTetra says (YL_V9_ALLOW_3D=1 switches the old 3D step on)
set tetra_ignored 0
if {$DO_TETRA && !([info exists ::env(YL_V9_ALLOW_3D)] && $::env(YL_V9_ALLOW_3D) eq "1")} { set DO_TETRA 0; set tetra_ignored 1 }
if {[info exists ::YL_ELEM_TYPE]} { set ABAQUS_ELEM_TYPE $::YL_ELEM_TYPE }
if {[info exists ::YL_ASPECT]}    { set TARGET_ASPECT $::YL_ASPECT }
if {[info exists ::YL_OUT_DIR]}   { set OUT_DIR $::YL_OUT_DIR }
if {[info exists ::YL_BASE_NAME]} { set BASE_NAME $::YL_BASE_NAME }
if {[info exists ::YL_V8_FALLBACK]} { set V8_FALLBACK $::YL_V8_FALLBACK }
if {[info exists ::env(YL_FILLET_ROWS)] && $::env(YL_FILLET_ROWS) in {altair dense}} { set FILLET_ROWS $::env(YL_FILLET_ROWS) }
if {[info exists ::YL_FILLET_ROWS] && $::YL_FILLET_ROWS in {altair dense}} { set FILLET_ROWS $::YL_FILLET_ROWS }
if {$::YL_BATCH} {
    if {[info exists ::YL_ELEM_SIZE] && $::YL_ELEM_SIZE > 0.05} { set ELEM_SIZE [expr {double($::YL_ELEM_SIZE)}] }
} elseif {![catch {hm_getfloat "Enter Target Mesh Size (mm):" "Please enter target element size" $ELEM_SIZE} user_val]} {
    if {$user_val ne "" && $user_val > 0.05} { set ELEM_SIZE [expr {double($user_val)}] }
}
set ABAQUS_ELEM_TYPE [string toupper [string trim $ABAQUS_ELEM_TYPE]]
file mkdir $OUT_DIR

set yl9_DIR [file dirname [file normalize [info script]]]
source [file join $yl9_DIR "YL_V8_qclean.tcl"]
# quality metrics (ys_tria_stats / ys_tet_stats) without the report driver
set ::YS_LIB_ONLY 1
source [file join $yl9_DIR "YL_mesh_stats.tcl"]
unset ::YS_LIB_ONLY

# ---------------- helpers ----------------
proc yl9_count {cfgs} {
    *clearmark elems 1
    catch {eval *createmark elems 1 {"by config"} $cfgs}
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}
# trias above the aspect limit. NOT *elementtestaspect : *readqualitycriteria
# (needed by the BatchMesher) switches the session's aspect formula to
# longest edge / shortest height WITHOUT the sqrt(3)/2 factor, so trias of
# aspect 4.5 (HyperMesh default, what V8 checked and what the GUI shows after
# the file is opened again) were reported as 5.2. qc::tri is the default
# definition, conservative : max(longest edge / shortest height * sqrt(3)/2,
# longest edge / shortest edge).
proc yl9_bad_aspect {lim} {
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    set r 0
    array set XYZ {}
    foreach e $eids {
        set P {}
        foreach n [lrange [hm_nodelist $e] 0 2] {
            if {![info exists XYZ($n)]} { set XYZ($n) [lindex [hm_nodevalue $n] 0] }
            lappend P $XYZ($n)
        }
        if {[lindex [qc::tri {*}$P] 0] > $lim} { incr r }
    }
    return $r
}
# mode 0 : free edges, mode 1 : T-connections
proc yl9_edges {mode} {
    catch {*createmark comps 1 "^edges"; *deletemark comps 1}
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    set n -1
    if {![catch {*findedges1 elems 1 $mode}]} {
        *clearmark elems 2
        *createmark elems 2 "by comp name" "^edges"
        set n [llength [hm_getmark elems 2]]
        *clearmark elems 2
    }
    catch {*createmark comps 1 "^edges"; *deletemark comps 1}
    *clearmark elems 1
    return $n
}
proc yl9_delete_elems {cfgs} {
    *clearmark elems 1
    catch {eval *createmark elems 1 {"by config"} $cfgs}
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
    *clearmark elems 1
}
proc yl9_tetmesh {growth} {
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    set err ""
    if {[catch {
        *createstringarray 2 "pars: upd_shell fix_comp_bdr pre_cln post_cln" "tet: 291 $growth -1 0 0.8 0"
        *tetmesh elems 1 1 elems 0 -1 1 2
    } err]} { set err "tetmesh error: $err" } else { set err "" }
    *clearmark elems 1
    return $err
}
proc yl9_surf_elem_count {s} {
    *clearmark surfs 1
    *clearmark elems 2
    *createmark surfs 1 $s
    set n 0
    if {![catch {*findmark surfs 1 257 1 elems 0 2}]} { set n [llength [hm_getmark elems 2]] }
    *clearmark surfs 1
    *clearmark elems 2
    return $n
}
# surfaces with area but without elements (BatchMesher failures)
proc yl9_unmeshed {surf_ids} {
    set r {}
    foreach s $surf_ids {
        if {![hm_entityinfo exist surfs $s]} { continue }
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        if {$a > 1e-6 && [yl9_surf_elem_count $s] == 0} { lappend r $s }
    }
    return $r
}
proc yl9_avg_elem_size {n_tria} {
    if {$n_tria <= 0} { return 0.0 }
    *clearmark surfs 1
    *createmark surfs 1 all
    set area 0.0
    foreach s [hm_getmark surfs 1] {
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        set area [expr {$area + $a}]
    }
    *clearmark surfs 1
    return [expr {sqrt(4.0 / sqrt(3.0) * $area / $n_tria)}]
}

# ---------------- geometry helpers ----------------
proc yl9_vsub {a b} { list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}] }
proc yl9_vlen {a} { expr {sqrt([lindex $a 0]**2 + [lindex $a 1]**2 + [lindex $a 2]**2)} }
proc yl9_live {l} {
    set ss {}
    catch { set ss [hm_getsurfacesfromedge $l] }
    return [expr {[llength [lsort -unique $ss]] == 2}]
}
# {length polyline(nseg+1 points) turn_deg}
proc yl9_line_poly {l {nseg 64}} {
    set ts {}
    for {set k 0} {$k <= $nseg} {incr k} { lappend ts [expr {double($k) / $nseg}] }
    set pts {}
    catch { set pts [hm_getcoordinatesofpointsonline $l $ts] }
    set len 0.0
    catch { set len [hm_linelength $l] }
    set turn 0.0
    for {set i 1} {$i < [llength $pts] - 1} {incr i} {
        set d1 [yl9_vsub [lindex $pts $i] [lindex $pts [expr {$i-1}]]]
        set d2 [yl9_vsub [lindex $pts [expr {$i+1}]] [lindex $pts $i]]
        set l1 [yl9_vlen $d1]; set l2 [yl9_vlen $d2]
        if {$l1 < 1e-12 || $l2 < 1e-12} { continue }
        set c [expr {([lindex $d1 0]*[lindex $d2 0]+[lindex $d1 1]*[lindex $d2 1]+[lindex $d1 2]*[lindex $d2 2])/($l1*$l2)}]
        set turn [expr {$turn + acos(max(-1.0, min(1.0, $c))) * 180.0 / acos(-1.0)}]
    }
    return [list $len $pts $turn]
}

# ---------------- 0. trace clean-up (before the BatchMesher) ----------------
# Leftovers of features that were cut off in CAD (e.g. dimples on a pad that
# were removed by a cut) : crescent sliver surfaces ~0.02 mm thick sitting on an
# edge, and the edge between the two real faces broken into many pieces
# (1.6 mm segments, 0.02 / 0.005 mm steps, small arcs). Every piece end is a
# fixed vertex, so the mesh follows the trace instead of the clean edge.
#   1. sliver surface (area < TRACE_AREA x size^2 and width = area / longest
#      edge < TRACE_WIDTH x size) -> merged into its largest neighbour
#      (its edges to that neighbour suppressed; the geometry moves < 0.05 mm)
#   2. vertices around the slivers between two edges of the SAME two surfaces
#      -> suppressed (the pieces become one edge), repeated until nothing
#      changes. A vertex where both edges are longer than TRACE_KEEP_LEN x size
#      and the edge turns more than TRACE_KINK deg is a real corner : kept.
# Only the neighbourhood of the slivers (TRACE_ZONE x size) is touched.
proc yl9_point_xyz {p} {
    *clearmark lines 2
    if {[catch {*createmark lines 2 "by points" $p}]} { return {} }
    set ls [hm_getmark lines 2]
    *clearmark lines 2
    if {[llength $ls] == 0} { return {} }
    set ends [hm_getcoordinatesofpointsonline [lindex $ls 0] {0.0 1.0}]
    if {[llength $ls] == 1} { return [lindex $ends 0] }
    set other [hm_getcoordinatesofpointsonline [lindex $ls 1] {0.0 1.0}]
    set best {}; set bd 1e30
    foreach a $ends { foreach b $other { set d [yl9_vlen [yl9_vsub $a $b]]; if {$d < $bd} { set bd $d; set best $a } } }
    return $best
}
# tangent of line l leaving its end that lies at point p
proc yl9_end_tangent {l p} {
    set q [hm_getcoordinatesofpointsonline $l {0.0 0.02 0.98 1.0}]
    if {[yl9_vlen [yl9_vsub [lindex $q 0] $p]] < [yl9_vlen [yl9_vsub [lindex $q 3] $p]]} {
        set d [yl9_vsub [lindex $q 1] [lindex $q 0]]
    } else {
        set d [yl9_vsub [lindex $q 2] [lindex $q 3]]
    }
    set n [yl9_vlen $d]
    if {$n < 1e-12} { return {0 0 0} }
    return [list [expr {[lindex $d 0]/$n}] [expr {[lindex $d 1]/$n}] [expr {[lindex $d 2]/$n}]]
}
proc yl9_clean_traces {size} {
    set area_lim  [expr {$::TRACE_AREA  * $size * $size}]
    set width_lim [expr {$::TRACE_WIDTH * $size}]
    set zone      [expr {$::TRACE_ZONE  * $size}]
    set keep_len  [expr {$::TRACE_KEEP_LEN * $size}]
    # 1. slivers
    *clearmark surfs 1
    *createmark surfs 1 all
    set slivers {}
    foreach s [hm_getmark surfs 1] {
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        if {$a <= 0.0 || $a >= $area_lim} { continue }
        set eds [lsort -unique [join [hm_getsurfaceedges $s]]]
        set lmax 0.0
        foreach l $eds { set ll [hm_linelength $l]; if {$ll > $lmax} { set lmax $ll } }
        if {$lmax <= 0.0 || $a / $lmax >= $width_lim} { continue }
        lappend slivers [list $s $eds]
    }
    *clearmark surfs 1
    if {[llength $slivers] == 0} { return [list 0 0] }
    set centers {}
    set sup {}
    foreach x $slivers {
        lassign $x s eds
        set best ""; set ba -1.0
        foreach l $eds {
            lappend centers [lindex [hm_getcoordinatesofpointsonline $l {0.5}] 0]
            foreach o [hm_getsurfacesfromedge $l] {
                if {$o == $s} { continue }
                set ao 0.0
                catch { set ao [hm_getareaofsurface surfs $o] }
                if {$ao > $ba} { set ba $ao; set best $o }
            }
        }
        if {$best eq ""} { continue }
        foreach l $eds { if {$best in [hm_getsurfacesfromedge $l]} { lappend sup $l } }
    }
    set n_sliver 0
    if {[llength $sup] > 0} {
        set before [yl9_surf_total]
        *clearmark lines 1
        eval *createmark lines 1 [lsort -unique -integer $sup]
        catch { *edgesmarksuppress 1 179.0 }
        *clearmark lines 1
        set n_sliver [expr {$before - [yl9_surf_total]}]
    }
    # 2. vertices near the slivers
    set n_vert 0
    for {set pass 0} {$pass < 8} {incr pass} {
        set cand {}
        *clearmark lines 1
        *createmark lines 1 all
        foreach l [hm_getmark lines 1] {
            if {![yl9_live $l]} { continue }
            set m [lindex [hm_getcoordinatesofpointsonline $l {0.5}] 0]
            set near 0
            foreach c $centers { if {[yl9_vlen [yl9_vsub $m $c]] < $zone} { set near 1; break } }
            if {!$near} { continue }
            *clearmark points 2
            catch { *createmark points 2 "by lines" $l }
            lappend cand {*}[hm_getmark points 2]
            *clearmark points 2
        }
        *clearmark lines 1
        set vs {}
        foreach p [lsort -unique -integer $cand] {
            *clearmark lines 2
            if {[catch {*createmark lines 2 "by points" $p}]} { continue }
            set ls {}
            foreach l [hm_getmark lines 2] { if {[yl9_live $l]} { lappend ls $l } }
            *clearmark lines 2
            if {[llength $ls] != 2} { continue }
            lassign $ls l1 l2
            if {[lsort -integer -unique [hm_getsurfacesfromedge $l1]] ne [lsort -integer -unique [hm_getsurfacesfromedge $l2]]} { continue }
            if {[hm_linelength $l1] > $keep_len && [hm_linelength $l2] > $keep_len} {
                set xyz [yl9_point_xyz $p]
                if {$xyz ne {}} {
                    set t1 [yl9_end_tangent $l1 $xyz]; set t2 [yl9_end_tangent $l2 $xyz]
                    # tangents both point away from the vertex : straight = -1
                    set c [expr {-([lindex $t1 0]*[lindex $t2 0]+[lindex $t1 1]*[lindex $t2 1]+[lindex $t1 2]*[lindex $t2 2])}]
                    if {acos(max(-1.0, min(1.0, $c))) * 180.0 / acos(-1.0) > $::TRACE_KINK} { continue }
                }
            }
            lappend vs $p
        }
        if {[llength $vs] == 0} { break }
        *clearmark points 1
        eval *createmark points 1 $vs
        set rc [catch { *verticesmarksuppress 1 179.0 }]
        *clearmark points 1
        if {$rc} { break }
        incr n_vert [llength $vs]
    }
    return [list $n_sliver $n_vert]
}
proc yl9_surf_total {} {
    *clearmark surfs 1
    *createmark surfs 1 all
    set n [llength [hm_getmark surfs 1]]
    *clearmark surfs 1
    return $n
}

# ---------------- 3b. curved narrow bands -> structured mesh ----------------
# A narrow 4-sided band whose two short sides are strongly curved arcs (the
# rounded tip of a thin rib : R ~0.8 mm, arc turning ~150 deg, 20 mm long)
# got 2 elements over 150 deg from the BatchMesher, with the middle row not
# lined up : the trias twist across the curve (wavy / "울퉁불퉁" surface).
# Such a band (and ONLY such a band) is meshed again as a structured grid:
#   - the two long edges (rails) get the same node count (the one with fewer
#     nodes gets nodes inserted on the CAD line, neighbour trias split too)
#   - the arcs get k elements : one per BAND_ARC_DEG of turn, as long as the
#     element stays above the minimum length and below BAND_MAX_ASPECT
#   - interior nodes : transfinite (Coons) interpolation of the four edges,
#     snapped onto the CAD surface -> rows follow the geometry
# A band is left as it is when the result would contain a tria above the limit.
# join the pieces (list of {id len pts turn}) that run from point p0 to one of
# the target points into one side : {ids len pts turn piece_ids}, {} if none
proc yl9_chain {p0 pieces targets} {
    # geometry clean-up tolerance of the import (CleanupTol) : piece ends may differ by that much
    set tol 0.01
    set pts [list $p0]; set used {}; set len 0.0
    set cur $p0
    for {set step 0} {$step < 4} {incr step} {
        foreach t $targets { if {[yl9_vlen [yl9_vsub $cur $t]] < $tol && $step > 0} {
            set turn [lindex [yl9_turn_of $pts] 0]
            return [list [join $used _] $len $pts $turn $used]
        } }
        set found 0
        foreach pc $pieces {
            lassign $pc id ll pp
            if {$id in $used} { continue }
            if {[yl9_vlen [yl9_vsub [lindex $pp 0] $cur]] < $tol} {
                set seg $pp
            } elseif {[yl9_vlen [yl9_vsub [lindex $pp end] $cur]] < $tol} {
                set seg [lreverse $pp]
            } else { continue }
            lappend pts {*}[lrange $seg 1 end]
            lappend used $id
            set len [expr {$len + $ll}]
            set cur [lindex $seg end]
            set found 1
            break
        }
        if {!$found} { return {} }
    }
    return {}
}
proc yl9_turn_of {pts} {
    set turn 0.0
    for {set i 1} {$i < [llength $pts] - 1} {incr i} {
        set d1 [yl9_vsub [lindex $pts $i] [lindex $pts [expr {$i-1}]]]
        set d2 [yl9_vsub [lindex $pts [expr {$i+1}]] [lindex $pts $i]]
        set l1 [yl9_vlen $d1]; set l2 [yl9_vlen $d2]
        if {$l1 < 1e-12 || $l2 < 1e-12} { continue }
        set c [expr {([lindex $d1 0]*[lindex $d2 0]+[lindex $d1 1]*[lindex $d2 1]+[lindex $d1 2]*[lindex $d2 2])/($l1*$l2)}]
        set turn [expr {$turn + acos(max(-1.0, min(1.0, $c))) * 180.0 / acos(-1.0)}]
    }
    return [list $turn]
}
proc yl9_band_dbg {txt} {
    if {![info exists ::env(YL_DEBUG_BANDS)] || $::env(YL_DEBUG_BANDS) eq ""} { return }
    set fh [open $::env(YL_DEBUG_BANDS) a]; puts $fh $txt; close $fh
}
proc yl9_find_bands {size} {
    set r {}
    *clearmark surfs 1
    *createmark surfs 1 all
    set sids [hm_getmark surfs 1]
    *clearmark surfs 1
    foreach s $sids {
        set eds {}
        foreach l [lsort -unique [join [hm_getsurfaceedges $s]]] { if {[yl9_live $l]} { lappend eds $l } }
        # 4 sides; a short side may be split into a few pieces (e.g. a 0.5 mm
        # piece at one end) -> up to 3 pieces per side are joined into one chain
        if {[llength $eds] < 4 || [llength $eds] > 8} { continue }
        set info {}
        foreach l $eds { lappend info [concat $l [yl9_line_poly $l]] }
        set info [lsort -real -decreasing -index 1 $info]
        lassign $info A B
        set rest [lrange $info 2 end]
        if {[llength $rest] == 2} {
            lassign $rest C D
        } else {
            set C [yl9_chain [lindex [lindex $A 2] 0] $rest [list [lindex [lindex $B 2] 0] [lindex [lindex $B 2] end]]]
            set D [yl9_chain [lindex [lindex $A 2] end] $rest [list [lindex [lindex $B 2] 0] [lindex [lindex $B 2] end]]]
            if {$C eq {} || $D eq {} || [llength [lindex $C 4]] + [llength [lindex $D 4]] != [llength $rest]} { yl9_band_dbg "surf $s: sides not chained (C=[lindex $C 0] D=[lindex $D 0])"; continue }
        }
        set la [lindex $A 1]; set lb [lindex $B 1]; set lc [lindex $C 1]; set ld [lindex $D 1]
        if {$lb < 0.8 * $la} { yl9_band_dbg "surf $s: rails differ $la $lb"; continue }
        if {max($lc, $ld) * $::BAND_MIN_RATIO > $lb || max($lc, $ld) > 3.0 * $size} { yl9_band_dbg "surf $s: not narrow ($lc $ld / $lb)"; continue }
        if {min([lindex $C 3], [lindex $D 3]) < $::BAND_MIN_TURN} { yl9_band_dbg "surf $s: arcs turn [lindex $C 3] [lindex $D 3]"; continue }
        # rails opposite : the caps join them end to end
        set pa [lindex $A 2]; set pb [lindex $B 2]
        set tol [expr {max(1e-3, 0.01 * $lc)}]
        set ok 1
        foreach cap [list $C $D] {
            set pc [lindex $cap 2]
            set e0 [lindex $pc 0]; set e1 [lindex $pc end]
            set ta 0; set tb 0
            foreach e [list $e0 $e1] {
                foreach q [list [lindex $pa 0] [lindex $pa end]] { if {[yl9_vlen [yl9_vsub $e $q]] < $tol} { incr ta } }
                foreach q [list [lindex $pb 0] [lindex $pb end]] { if {[yl9_vlen [yl9_vsub $e $q]] < $tol} { incr tb } }
            }
            if {$ta != 1 || $tb != 1} { set ok 0 }
        }
        if {!$ok} { yl9_band_dbg "surf $s: sides not opposite"; continue }
        lappend r [list $s $A $B $C $D]
    }
    return $r
}
# mesh nodes (in qc memory) of the given elements lying on polyline pts,
# sorted along it : list of {node arc_position}
proc yl9_nodes_on {pts elems} {
    set total 0.0
    set a [lindex $pts 0]
    foreach b [lrange $pts 1 end] { set total [expr {$total + [yl9_vlen [yl9_vsub $b $a]]}]; set a $b }
    set tol [expr {max(1e-4, min(0.01, 0.002 * $total))}]
    array set seen {}
    set r {}
    foreach e $elems {
        if {![info exists qc::E($e)]} { continue }
        foreach n $qc::E($e) {
            if {[info exists seen($n)]} { continue }
            set seen($n) 1
            lassign [qc::poly_project $qc::X($n) $pts] d s
            if {$d < $tol} { lappend r [list $n $s] }
        }
    }
    return [lsort -real -index 1 $r]
}
proc yl9_band_elems {s} {
    set r {}
    foreach e [array names qc::E] { if {[qc::surf_of $e] eq $s} { lappend r $e } }
    return $r
}
# insert one node on the longest segment of the node chain `on` (on the CAD
# polyline pts); only the trias outside surface skip_s are checked against lim
proc yl9_split_longest {on pts skip_s lim} {
    set segs {}
    for {set i 0} {$i < [llength $on] - 1} {incr i} {
        set u [lindex $on $i 0]; set v [lindex $on [expr {$i+1}] 0]
        if {[llength [qc::edge_elems $u $v]] != 2} { continue }
        lappend segs [list $i [qc::dist $u $v]]
    }
    foreach sg [lsort -real -decreasing -index 1 $segs] {
        set i [lindex $sg 0]
        set u [lindex $on $i 0]; set v [lindex $on [expr {$i+1}] 0]
        lassign $qc::X($u) ux uy uz; lassign $qc::X($v) vx vy vz
        set pm [lindex [qc::poly_project [list [expr {0.5*($ux+$vx)}] [expr {0.5*($uy+$vy)}] [expr {0.5*($uz+$vz)}]] $pts] 2]
        set bad 0
        foreach e [qc::edge_elems $u $v] {
            if {[qc::surf_of $e] eq $skip_s} { continue }
            foreach rep [list $u $v] {
                set p {}
                foreach m $qc::E($e) { lappend p [expr {$m == $rep ? $pm : $qc::X($m)}] }
                lassign [qc::tri {*}$p] asp nn ar
                if {$ar < 1e-12 || $asp > $lim} { set bad 1 }
            }
        }
        if {$bad} { continue }
        set m [qc::do_split $u $v $pm]
        return [linsert $on [expr {$i+1}] [list $m [expr {0.5*([lindex $on $i 1]+[lindex $on [expr {$i+1}] 1])}]]]
    }
    return {}
}
# point of polyline pts at arc length position s (from its start)
proc yl9_point_at {pts s} {
    set acc 0.0
    set a [lindex $pts 0]
    foreach b [lrange $pts 1 end] {
        set d [yl9_vlen [yl9_vsub $b $a]]
        if {$acc + $d >= $s && $d > 1e-12} {
            set t [expr {($s - $acc) / $d}]
            return [list [expr {[lindex $a 0]+$t*([lindex $b 0]-[lindex $a 0])}] [expr {[lindex $a 1]+$t*([lindex $b 1]-[lindex $a 1])}] [expr {[lindex $a 2]+$t*([lindex $b 2]-[lindex $a 2])}]]
        }
        set acc [expr {$acc + $d}]
        set a $b
    }
    return [lindex $pts end]
}
# normalised arc length positions of a node chain (0 .. 1)
proc yl9_params {nodes} {
    set s {0.0}; set t 0.0
    for {set i 1} {$i < [llength $nodes]} {incr i} { set t [expr {$t + [qc::dist [lindex $nodes [expr {$i-1}]] [lindex $nodes $i]]}]; lappend s $t }
    set r {}; foreach v $s { lappend r [expr {$t > 0 ? $v / $t : 0.0}] }
    return $r
}
# one structured remesh of band s with k rows across the arcs (qc memory);
# returns {1 note} or {0 reason} - the caller restores the snapshot on 0
proc yl9_band_attempt {s k turn kcur lc onA onB onC onD pa pb pc pd split_lim} {
    # equal node counts : rails, then the arcs (neighbour trias checked against split_lim)
    set fail 0
    while {!$fail && [llength $onA] < [llength $onB]} { set onA [yl9_split_longest $onA $pa $s $split_lim]; if {$onA eq {}} { set fail 1 } }
    while {!$fail && [llength $onB] < [llength $onA]} { set onB [yl9_split_longest $onB $pb $s $split_lim]; if {$onB eq {}} { set fail 1 } }
    while {!$fail && [llength $onC] < $k + 1} { set onC [yl9_split_longest $onC $pc $s $split_lim]; if {$onC eq {}} { set fail 1 } }
    while {!$fail && [llength $onD] < $k + 1} { set onD [yl9_split_longest $onD $pd $s $split_lim]; if {$onD eq {}} { set fail 1 } }
    if {$fail || [llength $onC] != $k + 1 || [llength $onD] != $k + 1} {
        return [list 0 "surf $s: node insertion refused ($k rows)"]
    }
    # orientation : A and B run from cap C to cap D, C and D from rail A to rail B
    set nA {}; foreach x $onA { lappend nA [lindex $x 0] }
    set nB {}; foreach x $onB { lappend nB [lindex $x 0] }
    set nC {}; foreach x $onC { lappend nC [lindex $x 0] }
    set nD {}; foreach x $onD { lappend nD [lindex $x 0] }
    if {[lsearch -exact $nC [lindex $nA 0]] < 0} { set nA [lreverse $nA] }
    if {[lsearch -exact $nC [lindex $nB 0]] < 0} { set nB [lreverse $nB] }
    if {[lindex $nC 0] ne [lindex $nA 0]} { set nC [lreverse $nC] }
    if {[lindex $nD 0] ne [lindex $nA end]} { set nD [lreverse $nD] }
    if {[lindex $nC 0] ne [lindex $nA 0] || [lindex $nC end] ne [lindex $nB 0] ||
        [lindex $nD 0] ne [lindex $nA end] || [lindex $nD end] ne [lindex $nB end]} {
        return [list 0 "surf $s: corners not found"]
    }
    # old elements : orientation reference (centroid, normal), then removed
    set ref {}
    foreach e [yl9_band_elems $s] {
        lassign $qc::E($e) p q r
        set c [list [expr {([lindex $qc::X($p) 0]+[lindex $qc::X($q) 0]+[lindex $qc::X($r) 0])/3.0}] \
                    [expr {([lindex $qc::X($p) 1]+[lindex $qc::X($q) 1]+[lindex $qc::X($r) 1])/3.0}] \
                    [expr {([lindex $qc::X($p) 2]+[lindex $qc::X($q) 2]+[lindex $qc::X($r) 2])/3.0}]]
        lappend ref [list $c [lindex [qc::etri $qc::E($e)] 1]]
        qc::del_elem $e
    }
    # arc nodes evenly spaced along the CAD arc (the inserted ones sit at
    # segment midpoints : 1/2, 1/4, 1/4 ...) -> every row turns the same
    # angle. A node is moved only where the neighbour trias stay valid.
    foreach chain [list $nC $nD] cpts [list $pc $pd] {
        set s0 [lindex [qc::poly_project $qc::X([lindex $chain 0]) $cpts] 1]
        set s1 [lindex [qc::poly_project $qc::X([lindex $chain end]) $cpts] 1]
        for {set j 1} {$j < $k} {incr j} {
            set nd [lindex $chain $j]
            set target [yl9_point_at $cpts [expr {$s0 + ($s1 - $s0) * double($j) / $k}]]
            set old $qc::X($nd)
            set nold {}
            foreach e $qc::NE($nd) { lappend nold [lindex [qc::etri $qc::E($e)] 1] }
            set qc::X($nd) $target
            set i 0
            foreach e $qc::NE($nd) {
                lassign [qc::etri $qc::E($e)] asp nn ar
                if {$ar < 1e-12 || $asp > max($qc::LIM, $split_lim) || [qc::dot $nn [lindex $nold $i]] < 0.5} { set qc::X($nd) $old; break }
                incr i
            }
        }
    }
    # arc length parameters
    set n [expr {[llength $nA] - 1}]
    set sA [yl9_params $nA]; set sB [yl9_params $nB]; set tC [yl9_params $nC]; set tD [yl9_params $nD]
    array unset G
    for {set i 0} {$i <= $n} {incr i} { set G($i,0) [lindex $nA $i]; set G($i,$k) [lindex $nB $i] }
    for {set j 0} {$j <= $k} {incr j} { set G(0,$j) [lindex $nC $j]; set G($n,$j) [lindex $nD $j] }
    set X00 $qc::X([lindex $nA 0]); set Xn0 $qc::X([lindex $nA end])
    set X0k $qc::X([lindex $nB 0]); set Xnk $qc::X([lindex $nB end])
    for {set i 1} {$i < $n} {incr i} {
        set su [expr {0.5 * ([lindex $sA $i] + [lindex $sB $i])}]
        set Ai $qc::X([lindex $nA $i]); set Bi $qc::X([lindex $nB $i])
        for {set j 1} {$j < $k} {incr j} {
            set tv [expr {0.5 * ([lindex $tC $j] + [lindex $tD $j])}]
            set Cj $qc::X([lindex $nC $j]); set Dj $qc::X([lindex $nD $j])
            set p {}
            foreach d {0 1 2} {
                lappend p [expr {(1-$su)*[lindex $Cj $d] + $su*[lindex $Dj $d] + (1-$tv)*[lindex $Ai $d] + $tv*[lindex $Bi $d] \
                    - ((1-$su)*(1-$tv)*[lindex $X00 $d] + $su*(1-$tv)*[lindex $Xn0 $d] + (1-$su)*$tv*[lindex $X0k $d] + $su*$tv*[lindex $Xnk $d])}]
            }
            lassign $p px py pz
            if {![catch {hm_findclosestpointonsurface $px $py $pz $s} cp] && [llength $cp] >= 3} {
                set q [lrange $cp 0 2]
                if {[yl9_vlen [yl9_vsub $q $p]] < 0.5 * $lc} { set p $q }
            }
            set G($i,$j) [qc::new_node $p]
        }
    }
    # two trias per cell (shorter diagonal), oriented like the old mesh
    set worst 0.0
    for {set i 0} {$i < $n} {incr i} {
        for {set j 0} {$j < $k} {incr j} {
            set q00 $G($i,$j); set q10 $G([expr {$i+1}],$j); set q11 $G([expr {$i+1}],[expr {$j+1}]); set q01 $G($i,[expr {$j+1}])
            if {[qc::dist $q00 $q11] <= [qc::dist $q10 $q01]} {
                set tris [list [list $q00 $q10 $q11] [list $q00 $q11 $q01]]
            } else {
                set tris [list [list $q00 $q10 $q01] [list $q10 $q11 $q01]]
            }
            foreach t $tris {
                lassign [qc::etri $t] asp nn ar
                set c {0 0 0}
                foreach m $t { set c [list [expr {[lindex $c 0]+[lindex $qc::X($m) 0]/3.0}] [expr {[lindex $c 1]+[lindex $qc::X($m) 1]/3.0}] [expr {[lindex $c 2]+[lindex $qc::X($m) 2]/3.0}]] }
                set bd 1e30; set bn {0 0 1}
                foreach rf $ref { set d [yl9_vlen [yl9_vsub [lindex $rf 0] $c]]; if {$d < $bd} { set bd $d; set bn [lindex $rf 1] } }
                if {[qc::dot $nn $bn] < 0} { set t [list [lindex $t 0] [lindex $t 2] [lindex $t 1]] }
                if {$asp > $worst} { set worst $asp }
                qc::add_elem $t $s
            }
        }
    }
    if {$worst > $qc::LIM} {
        return [list 0 [format "surf %d: structured mesh refused (%d rows, aspect %.2f)" $s $k $worst]]
    }
    # boundary nodes of the new grid : kept by the clean-up (no collapse)
    set keep {}
    foreach m [concat $nA $nB $nC $nD] { lappend keep $qc::X($m) }
    return [list 1 [format "surf %d: %d x %d structured (arc %.0f deg in %d -> %d elements, rails %d nodes)" $s $n $k $turn $kcur $k [expr {$n + 1}]] $keep]
}

# relaxed = 1 : node insertion may make a neighbour tria worse than the limit
# (up to BAND_RELAX x limit); the clean-up repairs it afterwards with the band
# edge nodes kept. Returns {n_done notes keep_points}.
proc yl9_remap_bands {size lim {relaxed 0}} {
    set bands [yl9_find_bands $size]
    if {[llength $bands] == 0} { return [list 0 {}] }
    set qc::COSF [expr {cos(25.0 * acos(-1.0) / 180.0)}]
    if {[qc::load] == 0} { return [list 0 {}] }
    qc::classify
    set qc::LIM [expr {$lim * 0.97}]
    set done 0
    set notes {}
    set keep_pts {}
    foreach b $bands {
        lassign $b s A B C D
        set snap [qc::snapshot]
        set els [yl9_band_elems $s]
        if {[llength $els] == 0} { continue }
        set pa [lindex $A 2]; set pb [lindex $B 2]; set pc [lindex $C 2]; set pd [lindex $D 2]
        set onA [yl9_nodes_on $pa $els]; set onB [yl9_nodes_on $pb $els]
        set onC [yl9_nodes_on $pc $els]; set onD [yl9_nodes_on $pd $els]
        if {[llength $onA] < 2 || [llength $onB] < 2 || [llength $onC] < 2 || [llength $onD] < 2} { yl9_band_dbg "surf $s: edge nodes not found ([llength $onA] [llength $onB] [llength $onC] [llength $onD])"; continue }
        set kcur [expr {max(1, min([llength $onC], [llength $onD]) - 1)}]
        # rows across the arcs
        set lc [expr {max([lindex $C 1], [lindex $D 1])}]
        set turn [expr {max([lindex $C 3], [lindex $D 3])}]
        set nrail [expr {max([llength $onA], [llength $onB]) - 1}]
        set a [expr {max([lindex $A 1], [lindex $B 1]) / $nrail}]
        set k [expr {int(ceil($turn / $::BAND_ARC_DEG))}]
        set k [expr {min($k, int(floor($lc / ($::MIN_LEN_FACTOR * $size))))}]
        while {$k > 2} {
            set bw [expr {$lc / $k}]
            set hyp [expr {sqrt($a*$a + $bw*$bw)}]
            if {$hyp / min($a, $bw) <= $::BAND_MAX_ASPECT} { break }
            incr k -1
        }
        # remeshed when the BatchMesher mesh of the band is too coarse across the
        # arc (more than BAND_TRIGGER_DEG per element) or not a clean structured
        # grid (edge counts differ, or not 2 x columns x rows trias : the rows
        # wander across the arc -> wavy surface)
        set structured [expr {[llength $onA] == [llength $onB] && [llength $onC] == [llength $onD] && [llength $els] == 2 * $nrail * $kcur}]
        if {$structured && $turn / $kcur <= $::BAND_TRIGGER_DEG} { yl9_band_dbg [format "surf %s: already structured, arc %.0f deg in %d elements" $s $turn $kcur]; continue }
        # rows : the wished count first, fewer when the neighbour trias or the
        # band cells would get above the limit (down to the current count : a
        # clean grid instead of wandering rows)
        # never fewer than 2 rows : one element across a 120+ deg arc is no fix
        set res [list 0 "surf $s: not remeshed"]
        set split_lim [expr {$relaxed ? $::BAND_RELAX * $qc::LIM : $qc::LIM}]
        for {set kt [expr {max($k, $kcur)}]} {$kt >= max(2, min($k, $kcur))} {incr kt -1} {
            set res [yl9_band_attempt $s $kt $turn $kcur $lc $onA $onB $onC $onD $pa $pb $pc $pd $split_lim]
            if {[lindex $res 0]} { break }
            qc::restore $snap
        }
        if {[lindex $res 0]} { incr done; lappend keep_pts {*}[lindex $res 2] }
        lappend notes [lindex $res 1]
    }
    qc::commit
    return [list $done $notes $keep_pts]
}
# HyperMesh node ids of the trias at the given points (after qc::commit the
# in-memory ids of new nodes are gone)
proc yl9_nodes_at {pts} {
    array set H {}
    foreach p $pts { set H([format "%.5f,%.5f,%.5f" {*}$p]) 1 }
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    *clearmark nodes 2
    catch { *findmark elems 1 1 1 nodes 0 2 }
    set r {}
    foreach nd [hm_getmark nodes 2] {
        if {[info exists H([format "%.5f,%.5f,%.5f" {*}[lindex [hm_nodevalue $nd] 0]])]} { lappend r $nd }
    }
    *clearmark elems 1
    *clearmark nodes 2
    return $r
}

# ---------------- 1. criteria + parameter file ----------------
# Altair hm/batchmesh/tetra_5mm.criteria / ModelBuild/tetra_5mm.param scaled
# from 5 mm to the target size s (lengths x s/5).
proc yl9_write_bm_files {dir stem s feature_angle min_f max_f} {
    set cr [file join $dir "${stem}.criteria"]
    set pr [file join $dir "${stem}.param"]
    set f [open $cr w]
    puts $f "This Criteria file was created with Criteria Editor application\n"
    puts $f " #    Criterion     On  Wt   Ideal    Good    Warn    Fail   Worst  Solver \n"
    puts $f " 0 penalty value               0.00    0.00    1.00    2.00   10.00         "
    # min length : ideal = target, fail = min_f x target (smallest element the
    # mesher may create on a fillet / narrow face)
    puts $f [format " 1 min length         1 2.0 %7.3f %7.3f %7.3f %7.3f %7.3f       2 " \
        $s [expr {0.6*$s}] [expr {0.4*$s}] [expr {$min_f*$s}] [expr {0.8*$min_f*$s}]]
    puts $f [format " 2 max length         1 1.5 %7.3f %7.3f %7.3f %7.3f %7.3f         " \
        $s [expr {1.24*$s}] [expr {1.46*$s}] [expr {$max_f*$s}] [expr {1.25*$max_f*$s}]]
    puts $f " 3 aspect ratio       1 1.0    1.00    2.00    3.00    5.00   10.00       0 "
    puts $f " 4 warpage            1 2.0    0.00    5.00   15.00   20.00   40.00       0 "
    puts $f " 5 max angle quad     1 1.0   90.00  110.00  140.00  150.00  165.00       0 "
    puts $f " 6 min angle quad     1 1.0   90.00   70.00   40.00   30.00   15.00       0 "
    puts $f " 7 max angle tria     1 1.0   60.00   80.00  110.00  130.00  155.00       0 "
    puts $f " 8 min angle tria     1 1.0   60.00   50.00   30.00   20.00   10.00       0 "
    puts $f " 9 skew               1 1.5    0.00   10.00   40.00   60.00   75.00       0 "
    puts $f "10 jacobian           1 2.0    1.00    0.90    0.60    0.50    0.35       0 "
    puts $f "11 chordal dev        0 0.0    0.00    0.30    0.80    1.00    2.00         "
    puts $f "12 % of trias         0 2.0    0.00    3.00    4.50    6.00    9.00         "
    puts $f "13 taper              0   1    0.00    0.20    0.50    0.60    0.90       0 "
    close $f
    # fillet rows by fillet width (arc length) : tetra_5mm table x s/5
    #   width 0.9-1.8 s : 1 row, 1.8-4 s : 2 rows, 4-6 s : 3 rows,
    #   6-10 s : chordal deviation only (rows = max(table, chordal))
    set k [expr {$s / 5.0}]
    if {$::FILLET_ROWS eq "dense"} {
        # hand mesh style : small fillets get 2 / 3 rows of narrow elements as
        # well (the hand meshes go down to ~0.25 x size across a fillet)
        set ftab [join [list \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(2) chordal_deviation(%.4g)" [expr {4*$k}]  [expr {1.25*$k}] [expr {4.5*$k}] [expr {0.6*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(3) chordal_deviation(%.4g)" [expr {10*$k}] [expr {4.5*$k}]  [expr {9*$k}]   [expr {0.6*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(3) chordal_deviation(%.4g)" [expr {30*$k}] [expr {9*$k}]    [expr {20*$k}]  [expr {0.6*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(4) chordal_deviation(%.4g)" [expr {40*$k}] [expr {20*$k}]   [expr {30*$k}]  [expr {0.4*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do chordal_deviation(%.4g)"          [expr {50*$k}] [expr {30*$k}]   [expr {50*$k}]  [expr {0.3*$k}]]] "\n"]
    } else {
    set ftab [join [list \
        [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(1) chordal_deviation(%.4g)" [expr {10*$k}] [expr {4.5*$k}] [expr {9*$k}]  [expr {1.5*$k}]] \
        [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(2) chordal_deviation(%.4g)" [expr {30*$k}] [expr {9*$k}]   [expr {20*$k}] [expr {1.2*$k}]] \
        [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(3) chordal_deviation(%.4g)" [expr {40*$k}] [expr {20*$k}]  [expr {30*$k}] [expr {0.8*$k}]] \
        [format "rad(0,%.4g) wid(%.4g,%.4g) do chordal_deviation(%.4g)"          [expr {50*$k}] [expr {30*$k}]  [expr {50*$k}] [expr {0.4*$k}]]] "\n"]
    }
    set f [open $pr w]
    puts $f "#This Param file was created with Criteria/Param Editor application
#flags:
geometry_cleanup_flag             1
meshing_flag                      1
#mesh:
element_size                      $s
element_type                      0
mesh_align                        3
element_order                     1
surf_component                    1
feature_angle                     $feature_angle
# Holes Design Table:
holes_table_begin
appl_surf(0)
appl_solid(0)
appl_cordsfiles(0)
appl_flanged_suppr(0)
flanged_suppr_height(2.0)
narrow_slots_type(0)
abs_fixed_nodes_count_max(-8)
holes_table_end
#edge fillets:
edge_fillet_recognition           0
max_fillet_radius                 [format %.4g [expr {12*$k}]]
#surface fillets:
surface_fillet_table_begin
surface_fillet_recognition(1)
minimize_transitions(1)
$ftab
surface_fillet_table_end
#delete duplicates:
del_dupl_surfs_flag               2
del_dupl_surfs_tol                -1
#edges stitch:
edges_stitch_flags                0
max_edges_stitch_tol              -1.0
#fix overlapping surfs:
fix_overlapsurfs_flag             1
overlapsurfs_maxtangangle         -1.0
#narrow surfaces:
narrow_surfs_merge_width          -1.0
narrow_surfs_treat_flags          3
#beads:
beads_suppression                 0
beads_recognition                 0
minimal_beads_height              [format %.4g [expr {2*$k}]]
beads_treat_flags                 0
#flanges (sheet metal only):
flange_recognition                0
flange_elements_across            3
flange_max_width                  [format %.4g [expr {40*$k}]]
flange_min_width                  [format %.4g [expr {10*$k}]]
flanges_treat_flags               1
flange_max_remove_width           -1.0
#tria_reduction:
appl_tria_reduction               0
tria_redu_max_elemsize            auto
tria_redu_min_elemsize            auto
trias_around_holes                0
common_mesh_flow                  0
#thin solids:
extract_thinsolids                0
midsurf_method                    3
thinsolid_ratio                   0.3
max_thickness                     10.0
extract_feature_angle             25.0
#logo:
remove_logo                       0
logo_max_size                     30.0
logo_max_height                   1.5
logo_min_concavity                1.0
#threads:
threads_removal                   0
threads_toremove_max_depth        5.0
threads_replacediametertype       -2
#element cleanup:
folded_elems_angle                150.0
smooth_elems_target               0.2
fillets_mesh_flow                 1
#smoothing across edges options:
failed_elems_cleanup_flgs         8
move_nodes_across_feature_edges   1
featureedge_nodes_moveacross_max  [format %.4g [expr {0.05*$s}]]
move_nodes_across_free_edges      0
freeedge_nodes_moveacross_max     0.0
#correct warped elements:
move_normal_flag                  0
move_normal_dist                  [format %.4g [expr {0.25*$s}]]
divide_warped_quads               0
ignore_comps_boundary             0
gen_topology_prepare_flags        5
cleanup_comp_flag                 0
comp_select
use_wildcards_for_compsnames      0
cleanup_tolerances                auto"
    close $f
    return [list $cr $pr]
}

# ---------------- surfaces ----------------
*clearmark surfs 1
if {$::YL_BATCH} {
    *createmark surfs 1 "all"
} else {
    *createmarkpanel surfs 1 "Select surfaces for YL_V9 BatchMesher (Size = ${ELEM_SIZE}mm)"
}
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1
if {[llength $surf_ids] == 0 && !$::YL_BATCH} {
    *createmark surfs 1 "displayed"
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
}
if {[llength $surf_ids] == 0} {
    set ::YL_RESULT [list status "FAIL" msg "No surfaces found." engine "V9"]
    if {!$::YL_BATCH} { tk_messageBox -message "No surfaces found." }
    return
}

# old mesh on these surfaces / old tetras
array unset yl9_comps
foreach s $surf_ids {
    if {![catch {hm_getvalue surfs id=$s dataname=collector.id} cid] && $cid ne ""} { set yl9_comps($cid) 1 }
}
if {[array size yl9_comps] > 0} {
    *clearmark elems 1
    eval *createmark elems 1 {"by comp id"} [array names yl9_comps]
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
}
yl9_delete_elems {tetra4 tetra10}
*clearmark elems 1
catch { *elementorder 1 }

set n_surf_raw [llength $surf_ids]
# working files (criteria / parameter file, the BatchMesher's *_res.txt and
# time_limit.txt) : log folder, so that the output folder holds the models only
set yl9_work_dir $OUT_DIR
if {[info exists ::YL_LOG_DIR] && $::YL_LOG_DIR ne ""} { set yl9_work_dir $::YL_LOG_DIR; file mkdir $yl9_work_dir }
set yl9_tmp "$yl9_work_dir/_yl9_[pid]"

# ---------------- 0. trace clean-up ----------------
set trace_err ""
set n_trace_slivers 0
set n_trace_vertices 0
if {[catch {yl9_clean_traces $ELEM_SIZE} trace_res]} {
    set trace_err $trace_res
} else {
    lassign $trace_res n_trace_slivers n_trace_vertices
}
*clearmark surfs 1
*createmark surfs 1 "all"
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1

# ---------------- 2. BatchMesher ----------------
set bm_err ""
set t_bm [clock milliseconds]
lassign [yl9_write_bm_files [file dirname $yl9_tmp] [file tail $yl9_tmp] $ELEM_SIZE $FEATURE_ANGLE $MIN_LEN_FACTOR $MAX_LEN_FACTOR] bm_criteria bm_param
*clearmark lines 1
*createmark lines 1 "all"
catch { *maskmark lines 1 }
*clearmark lines 1
# *readqualitycriteria (hmbm.tcl calls it before *hm_batchmesh) is NOT used by
# default : the BatchMesher reads the criteria file given to it (same mesh with
# and without, tested on 7 models), and the call changes the session's element
# check formulas (aspect without the sqrt(3)/2 factor) and the QI panel of the
# user's GUI session. YL_V9_READ_CRITERIA=1 switches it on.
if {[info exists ::env(YL_V9_READ_CRITERIA)] && $::env(YL_V9_READ_CRITERIA) eq "1"} {
    if {[catch {*readqualitycriteria $bm_criteria} err]} { set bm_err "readqualitycriteria: $err" }
}
*clearmark surfs 1
eval *createmark surfs 1 $surf_ids
if {$bm_err eq "" && [catch {*hm_batchmesh 1 $bm_criteria $bm_param} err]} { set bm_err "hm_batchmesh: $err" }
*clearmark surfs 1
set t_bm [expr {([clock milliseconds] - $t_bm) / 1000.0}]
catch { file delete $bm_criteria $bm_param }
# the BatchMesher also saves its own copy of the model (<model>_<criteria>_<param>.hm)
# next to its *_res.txt report : a duplicate of what this script saves at the end
foreach yl9_f [glob -nocomplain -directory $yl9_work_dir "*[file tail $yl9_tmp].param.hm"] { catch { file delete $yl9_f } }
# surface list after the clean-up (merged surfaces get new ids)
*clearmark surfs 1
*createmark surfs 1 "all"
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1
# quads (none expected with element_type 0) -> trias
*clearmark elems 1
*createmark elems 1 "by config" quad4 quad8
if {[llength [hm_getmark elems 1]] > 0} { catch { *splitelements 2 1 } }
*clearmark elems 1
*createmark elems 1 "by config" tria3
if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 [expr {0.001 * $ELEM_SIZE}] 1 0 0 } }
*clearmark elems 1

set n_raw    [yl9_count tria3]
set bad_raw  [yl9_bad_aspect $TARGET_ASPECT]
set free_raw [yl9_edges 0]
set t_raw    [yl9_edges 1]
set unmeshed [yl9_unmeshed $surf_ids]

# ---------------- 3. repair ----------------
set qc_err ""
set n_dbl 0
set n_bands 0
set band_notes {}
set band_err ""
array unset QS
array set QS {collapse 0 flip 0 split 0 quadsplit 0 needlesplit 0 spec 0 dblface 0 bad_after 0}
if {$n_raw > 0} {
    # zero volume double faces (two trias on the same nodes : a fin the
    # BatchMesher folded onto itself) -> both removed where the skin stays closed.
    # qc::run only looks for them while aspect failures are left.
    if {[catch {
        qc::load
        qc::classify
        set n_dbl [qc::remove_double_faces]
        if {$n_dbl > 0} { qc::commit }
    } err]} { set qc_err "double faces: $err" }
    # curved narrow bands (rounded rib tips) -> structured mesh, then clean-up.
    # pass 1 (relaxed) : arc nodes may be inserted even where a neighbour tria
    #   gets worse than the limit; the clean-up repairs it with the band edge
    #   nodes kept (no collapse). Anything left above the limit / skin not
    #   closed -> the state before the bands is loaded back and
    # pass 2 (strict) : only insertions that keep every neighbour below the limit.
    set qc::KEEP_SURF_EDGES 0
    set preband "${yl9_tmp}_preband.hm"
    catch { hm_answernext yes }
    set has_preband [expr {$qc_err eq "" && ![catch { *writefile "$preband" 1 }] && [file exists $preband]}]
    # (globals live on between the models of one batch)
    catch { unset band_retry_note }
    set keep_pts {}
    foreach relaxed [expr {$has_preband ? {1 0} : {0}}] {
        set band_err ""; set n_bands 0; set band_notes {}
        set keep {}
        if {$qc_err eq "" && [catch {yl9_remap_bands $ELEM_SIZE $TARGET_ASPECT $relaxed} band_res]} {
            set band_err $band_res
        } elseif {$qc_err eq ""} {
            lassign $band_res n_bands band_notes keep_pts
            catch { set keep [yl9_nodes_at $keep_pts] }
        }
        array unset qc::KEEP_NODE
        foreach nd $keep { set qc::KEEP_NODE($nd) 1 }
        array set QS {collapse 0 flip 0 split 0 quadsplit 0 needlesplit 0 spec 0 dblface 0 bad_after 0}
        if {$qc_err eq "" && [catch {qc::run $TARGET_ASPECT $ELEM_SIZE $CLEANUP_ANGLE} qc_res]} {
            set qc_err $qc_res
        } elseif {$qc_err eq ""} {
            array set QS $qc_res
        }
        array unset qc::KEEP_NODE
        if {!$relaxed || $n_bands == 0} { break }
        if {[yl9_bad_aspect $TARGET_ASPECT] == 0 && [yl9_edges 0] == 0 && [yl9_edges 1] == 0} { break }
        # relaxed pass left something -> back to the state before the bands
        catch { hm_answernext yes }
        if {[catch { *readfile "$preband" }]} { break }
        lappend band_notes "relaxed pass left trias above the limit - meshed again with strict node insertion"
        set band_retry_note [lindex $band_notes end]
    }
    catch { file delete $preband }
    if {[info exists band_retry_note]} { lappend band_notes $band_retry_note }
}

# ---------------- 4. 2D check ----------------
set n_2d_skin     [yl9_count tria3]
set bad_2d_cnt    [yl9_bad_aspect $TARGET_ASPECT]
set free_edge_cnt [yl9_edges 0]
set t_edge_cnt    [yl9_edges 1]
set avg_size 0.0
catch { set avg_size [yl9_avg_elem_size $n_2d_skin] }
if {[info exists ::env(YL_DEBUG_ASPECT)] && $::env(YL_DEBUG_ASPECT) ne ""} {
    # troubleshooting : the trias HyperMesh reports above the limit, with their
    # aspect by the qclean definition, their nodes and component
    set fh [open $::env(YL_DEBUG_ASPECT) a]
    *clearmark elems 1; *clearmark elems 2
    *createmark elems 1 "by config" tria3
    catch {*elementtestaspect elems 1 $TARGET_ASPECT 2 2 0 "2D Aspect Ratio"}
    puts $fh "=== $BASE_NAME : [llength [hm_getmark elems 2]] bad"
    foreach e [hm_getmark elems 2] {
        set P {}
        foreach n [hm_nodelist $e] { lappend P [lindex [hm_nodevalue $n] 0] }
        set c ""; catch { set c [hm_getvalue elems id=$e dataname=collector.name] }
        puts $fh "elem $e comp '$c' nodes [hm_nodelist $e] qc_asp [lindex [qc::tri {*}[lrange $P 0 2]] 0]"
        foreach p $P { puts $fh "    $p" }
    }
    *clearmark elems 1; *clearmark elems 2
    close $fh
}
set pass2d [expr {$bm_err eq "" && $n_2d_skin > 0 && $bad_2d_cnt == 0 && $free_edge_cnt == 0 && $t_edge_cnt == 0 && [llength $unmeshed] == 0 ? 1 : 0}]

# test hook : YL_V9_FORCE_FALLBACK=1 sends every model down the V8 fallback path
if {[info exists ::env(YL_V9_FORCE_FALLBACK)] && $::env(YL_V9_FORCE_FALLBACK) eq "1"} { set pass2d 0 }
# Skin not passed -> the model is flagged for the V8 engine (fallback_v8 = 1).
# V8 is NOT run in this HyperMesh session: the BatchMesher changes session-wide
# meshing settings, and V8 then meshed differently (tested: V8 alone on a model
# 31 raw aspect failures -> pass, after a BatchMesher run in the same session
# 66 -> fail). run_hmbatch.ps1 re-runs the flagged models with -Version V8 in a
# new hmbatch process and merges the rows (engine "V9>V8").
set fallback_v8 [expr {!$pass2d && $V8_FALLBACK ? 1 : 0}]
catch { *unmaskall2 }

# skin quality (before the tetra mesher, which deletes the skin)
set SQ [dict create n 0]
# ys_xyz caches coordinates by node id : clear it (ids repeat between the models of a batch)
array unset ::YX
catch { set SQ [ys_tria_stats [ys_elems_by_config tria3]] }
proc yl9_q {d k {fmt %.3f}} { if {[dict exists $d $k]} { return [format $fmt [dict get $d $k]] }; return "" }

# ---------------- 5. save 2D / run 3D ----------------
set n_tet 0
set tet_err ""
set tet_check "not run"
set bad_tet_collapse_cnt 0
set TQ [dict create n 0]
set mode_label [expr {$DO_TETRA ? "3D_${ABAQUS_ELEM_TYPE}" : "2D_Only"}]
set out_stem "${BASE_NAME}_${mode_label}_${ELEM_SIZE}mm"
if {[info exists ::YL_OUT_NAME] && [string trim $::YL_OUT_NAME] ne ""} { set out_stem [string trim $::YL_OUT_NAME] }
set out_hm "$OUT_DIR/${out_stem}.hm"
set out_inp ""
set inp_err ""
set EXPORT_INP 0
if {[info exists ::YL_EXPORT_INP]} { set EXPORT_INP $::YL_EXPORT_INP }
set write_failed 0
set write_err ""
set tet_skipped 0

if {!$DO_TETRA} {
    catch { hm_answernext yes }
    set write_failed [catch { *writefile "$out_hm" 1 } write_err]
} elseif {!$pass2d} {
    set tet_skipped 1
    set tet_check "skipped (2D not passed)"
    catch { hm_answernext yes }
    set write_failed [catch { *writefile "$out_hm" 1 } write_err]
} else {
    catch { *templatefileset "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d" }
    set tet_err [yl9_tetmesh $TET_GROWTH]
    set n_tet [yl9_count {tetra4 tetra10}]
    set tet_check [expr {$n_tet > 0 ? "OK ($n_tet tetras)" : "FAILED"}]
    if {$n_tet > 0} {
        array unset ::YX
        catch { set TQ [ys_tet_stats [ys_elems_by_config tetra4] 400000] }
        array unset ::YX
        if {$DELETE_2D_SKIN} { yl9_delete_elems {tria3 tria6} }
        # Abaqus type numbers from templates/feoutput/abaqus/standard.3d
        array set yl9_types {
            C3D4 {204 1} C3D4H {204 2} DC3D4 {204 3} C3D4E {204 4} DC3D4E {204 5}
            AC3D4 {204 6} FC3D4 {204 7} C3D4T {204 8} C3D4P {204 9}
            C3D10 {210 1} C3D10H {210 2} C3D10M {210 3} C3D10MH {210 4} DC3D10 {210 5}
            C3D10E {210 6} DC3D10E {210 7} AC3D10 {210 8} C3D10MP {210 9} C3D10MPH {210 10}
            C3D10MT {210 11} C3D10I {210 12} C3D10HS {210 13}
        }
        set yl9_type_unknown ""
        if {![info exists yl9_types($ABAQUS_ELEM_TYPE)]} {
            set yl9_type_unknown "unknown element type '$ABAQUS_ELEM_TYPE'"
            set ABAQUS_ELEM_TYPE "C3D4"
        }
        lassign $yl9_types($ABAQUS_ELEM_TYPE) yl9_cfg yl9_typ
        if {$yl9_cfg == 210} {
            *clearmark elems 1
            *createmark elems 1 "by config" tetra4
            # 2nd argument 1 : put the mid nodes on the CAD geometry
            if {[llength [hm_getmark elems 1]] > 0} { catch { *orderchangetosecond 1 1 } }
        }
        *clearmark elems 1
        *createmark elems 1 "by config" [expr {$yl9_cfg == 210 ? "tetra10" : "tetra4"}]
        set yl9_e0 [lindex [hm_getmark elems 1] 0]
        catch { *elementtype $yl9_cfg $yl9_typ }
        catch { *elementsettypes 1 }
        set tet_type ""
        catch { set tet_type [hm_getvalue elems id=$yl9_e0 dataname=typename] }
        *clearmark elems 1
        *clearmark elems 2
        *createmark elems 1 "by config" tetra4 tetra10
        if {![catch {*elementtesttetcollapse elems 1 $MIN_TET_COLLAPSE 2 2 "3D Tet Collapse"}]} {
            set bad_tet_collapse_cnt [llength [hm_getmark elems 2]]
        }
        *clearmark elems 1
        *clearmark elems 2
        set n_tet [yl9_count {tetra4 tetra10}]
        set tet_check "OK ([yl9_count tetra4] tetra4 + [yl9_count tetra10] tetra10, type $tet_type, tet collapse < $MIN_TET_COLLAPSE : $bad_tet_collapse_cnt)"
        if {$tet_type ne $ABAQUS_ELEM_TYPE} { set tet_err "element type is '$tet_type', wanted $ABAQUS_ELEM_TYPE" }
        if {$yl9_type_unknown ne ""} { set tet_err $yl9_type_unknown }
    }
    catch { hm_answernext yes }
    set write_failed [catch { *writefile "$out_hm" 1 } write_err]
    if {$EXPORT_INP && $n_tet > 0} {
        set out_inp "$OUT_DIR/${out_stem}.inp"
        catch { file delete $out_inp }
        catch { hm_answernext yes }
        if {[catch {
            *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"
            *feoutputwithdata "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d" $out_inp 0 0 2 1 2
        } inp_err]} { set inp_err "INP EXPORT FAILED: $inp_err" } else { set inp_err "" }
        if {$inp_err eq "" && ![file exists $out_inp]} { set inp_err "INP EXPORT FAILED: file not written" }
    }
}

# ---------------- 6. report ----------------
set msg [format "YL_V9 (2D only : HyperMesh BatchMesher skin -> trace clean-up -> repair) finished\n\n- Target Mesh Size: %.2f mm (min %.2f, max %.2f), feature angle %.0f deg\n- BatchMesher: %.1f s, surfaces %d -> %d after clean-up, unmeshed surfaces: %d\n- 2D Trias: %d (BatchMesher: %d)\n- Aspect > %.1f : %d (BatchMesher: %d)\n- Repair: %d double face(s) removed, %d collapses, %d flips, %d splits\n- Free Edges: %d, T-Edges: %d (BatchMesher: %d, %d)\n- 2D PASS: %s\n- Tetra mesh: %s\n\nSaved: %s" \
    $ELEM_SIZE [expr {$MIN_LEN_FACTOR * $ELEM_SIZE}] [expr {$MAX_LEN_FACTOR * $ELEM_SIZE}] $FEATURE_ANGLE \
    $t_bm $n_surf_raw [llength $surf_ids] [llength $unmeshed] \
    $n_2d_skin $n_raw $TARGET_ASPECT $bad_2d_cnt $bad_raw \
    $n_dbl $QS(collapse) $QS(flip) [expr {$QS(split) + $QS(quadsplit) + $QS(needlesplit)}] \
    $free_edge_cnt $t_edge_cnt $free_raw $t_raw [expr {$pass2d ? "YES" : "NO"}] $tet_check $out_hm]
append msg [format "\n- Average 2D element size: %.2f mm (target %.2f mm)" $avg_size $ELEM_SIZE]
append msg [format "\n- CAD traces (slivers of cut-off features): %d sliver surface(s) merged, %d vertex(es) removed so the edges follow the clean geometry" $n_trace_slivers $n_trace_vertices]
if {$trace_err ne ""} { append msg "\n    trace clean-up error (skipped): $trace_err" }
append msg "\n- Curved narrow bands (rounded rib tips) meshed structured: $n_bands"
foreach w [lrange $band_notes 0 19] { append msg "\n    $w" }
if {$band_err ne ""} { append msg "\n    band remesh error (skipped): $band_err" }
if {$bad_2d_cnt > 0 && [info exists qc::stuck]} { foreach w [lrange $qc::stuck 0 9] { append msg "\n    not repaired: $w" } }
if {[dict get $SQ n] > 0} {
    append msg [format "\n- Skin quality: q mean %.3f (1 = equilateral), min angle < 20 deg %.2f %%, aspect > 3 %.2f %%, max angle > 120 deg %.2f %%, size jump p95 %.2f" \
        [dict get $SQ q_mean] [dict get $SQ pct_ang_lt20] [dict get $SQ pct_asp_gt3] [dict get $SQ pct_ang_gt120] [dict get $SQ jump_p95]]
}
if {[dict get $TQ n] > 0} {
    append msg [format "\n- Tetra quality: aspect p95 %.2f, aspect > 5 %.2f %%, collapse < 0.15 %.3f %%, dihedral < 10 deg %.3f %%" \
        [dict get $TQ asp_p95] [dict get $TQ pct_asp_gt5] [dict get $TQ pct_col_lt015] [dict get $TQ pct_dih_lt10]]
}
if {$out_inp ne ""} { append msg "\nAbaqus deck: $out_inp" }
if {$tetra_ignored} { append msg "\nNOTE: 3D (tetra) requested but not run - V9 writes the 2D skin only, the tetra mesh is made by hand" }

set _status "OK"
set _why {}
if {$write_failed || ![file exists $out_hm]} { lappend _why "SAVE FAILED: $write_err" }
if {$inp_err ne ""}                          { lappend _why $inp_err }
if {$bm_err ne ""}                           { lappend _why "BATCHMESHER ERROR: $bm_err" }
if {$n_2d_skin == 0}                         { lappend _why "NO 2D ELEMENTS CREATED" }
if {[llength $unmeshed] > 0}                 { lappend _why "[llength $unmeshed] SURFACE(S) COULD NOT BE MESHED" }
if {$qc_err ne ""}                           { lappend _why "CLEAN-UP ERROR: $qc_err" }
if {$bad_2d_cnt != 0}                        { lappend _why "$bad_2d_cnt ELEMENT(S) STILL ABOVE ASPECT $TARGET_ASPECT" }
if {$free_edge_cnt != 0 || $t_edge_cnt != 0} { lappend _why "SKIN NOT CLOSED (free=$free_edge_cnt, T=$t_edge_cnt)" }
if {$tet_skipped}                            { lappend _why "2D NOT PASSED, 3D SKIPPED" }
if {$fallback_v8}                            { lappend _why "V8 FALLBACK REQUESTED (BatchMesher skin not passed)[expr {$::YL_BATCH ? "" : " - run YL_V8_batch.tcl on the original geometry"}]" }
if {$tet_check eq "FAILED"}                  { lappend _why "TETRA MESH NOT CREATED $tet_err" }
if {$tet_check ne "FAILED" && $tet_err ne ""} { lappend _why "3D: $tet_err" }
if {[llength $_why] > 0} {
    set _status "FAIL"
    foreach w $_why { append msg "\n*** $w" }
}
set ::YL_RESULT [list status $_status msg $msg out $out_hm size $ELEM_SIZE \
    etype [expr {$DO_TETRA ? $ABAQUS_ELEM_TYPE : "2D"}] inp $out_inp \
    surfs [llength $surf_ids] n2d_raw $n_raw bad2d_raw $bad_raw n2d $n_2d_skin bad2d $bad_2d_cnt \
    free_edges $free_edge_cnt t_edges $t_edge_cnt ntet $n_tet badtet $bad_tet_collapse_cnt \
    collapse $QS(collapse) flip $QS(flip) split [expr {$QS(split) + $QS(quadsplit) + $QS(needlesplit)}] \
    pass2d $pass2d defeatured [expr {$n_surf_raw - [llength $surf_ids]}] avg_size [format %.3f $avg_size] \
    engine "V9" mesher "BatchMesher" fallback_v8 $fallback_v8 \
    trace_slivers $n_trace_slivers trace_vertices $n_trace_vertices bands $n_bands \
    q_mean [yl9_q $SQ q_mean] ang_lt20 [yl9_q $SQ pct_ang_lt20 %.2f] asp_gt3 [yl9_q $SQ pct_asp_gt3 %.2f] \
    tet_asp_p95 [yl9_q $TQ asp_p95] tet_asp_gt5 [yl9_q $TQ pct_asp_gt5 %.2f] tet_col_lt015 [yl9_q $TQ pct_col_lt015 %.3f] \
    tet_dih_lt10 [yl9_q $TQ pct_dih_lt10 %.3f]]

if {!$::YL_BATCH} { tk_messageBox -message $msg }
'@

# ----- YL_V8_qclean.tcl -----
$YLEngineFiles['YL_V8_qclean.tcl'] = @'
# =====================================================================
# YL_qclean.tcl : tria shell clean-up so that NO element exceeds the
#                 aspect-ratio limit, while the shell stays a closed,
#                 manifold skin that *tetmesh accepts.
#
#   qc::run <aspect_limit> <elem_size> ?feature_angle_deg?
#       works on every tria3 in the model, returns a dict with statistics
#
# Method (all decisions are made on an in-memory copy of the mesh, then
# only the changed elements are deleted / re-created in HyperMesh):
#   collapse : merge the two nodes of a short edge (removes 2 trias)
#   flip     : swap the diagonal shared by two trias
#   split    : insert a node in the middle of a long edge (2 trias -> 4)
# An operation is applied only if
#   - it lowers the worst aspect ratio of the elements it touches,
#   - it keeps the surface manifold (no T-edges, no free edges),
#   - it does not fold elements over,
#   - it does not move sharp feature lines / corners of the part.
# =====================================================================
namespace eval qc {
    variable X; variable E; variable NE; variable FN; variable CORNER
    variable newE {}; variable delE {}; variable newN {}
    variable vid 0; variable SURF
    variable LIM 5.0; variable SIZE 2.0; variable COSF 0.9; variable TINY 0.24; variable WHY ok
    # 1 : an edge between trias of two different CAD surfaces is a feature line
    #     even when tangent (fillet borders) - its nodes stay on the CAD line
    variable KEEP_SURF_EDGES 0
    # 1 : the number of nodes on a feature line is frozen - no collapse of a
    #     feature edge (except tiny ones) and no split of a feature edge.
    #     Keeps the node counts that were seeded on the CAD lines.
    variable LOCK_FEAT_COUNT 0
    variable LINE_OF
    # KEEP_NODE(node) : node may not be collapsed away (splits next to it are
    # still allowed). Used for the interior nodes of curved CAD lines so the
    # clean-up cannot turn a fillet arc back into one straight element.
    variable KEEP_NODE
    variable stats
}

proc qc::sub {a b} { lassign $a ax ay az; lassign $b bx by bz; list [expr {$ax-$bx}] [expr {$ay-$by}] [expr {$az-$bz}] }
proc qc::dot {a b} { lassign $a ax ay az; lassign $b bx by bz; expr {$ax*$bx+$ay*$by+$az*$bz} }
proc qc::cross {a b} { lassign $a ax ay az; lassign $b bx by bz; list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}] }
proc qc::len {a} { expr {sqrt([dot $a $a])} }
proc qc::dist {n1 n2} { variable X; len [sub $X($n1) $X($n2)] }
proc qc::key {a b} { expr {$a < $b ? "$a,$b" : "$b,$a"} }

# aspect ratio + unit normal + area of the triangle (p1,p2,p3)
# aspect = longest edge / shortest altitude * sqrt(3)/2  (1.0 for equilateral),
# never smaller than longest edge / shortest edge -> covers needles and caps.
proc qc::tri {p1 p2 p3} {
    set e1 [sub $p2 $p1]; set e2 [sub $p3 $p2]; set e3 [sub $p1 $p3]
    set l1 [len $e1]; set l2 [len $e2]; set l3 [len $e3]
    set lmax [expr {max($l1,$l2,$l3)}]; set lmin [expr {min($l1,$l2,$l3)}]
    set c [cross $e1 [sub $p3 $p1]]
    set a2 [len $c]
    if {$a2 < 1e-12 || $lmin < 1e-9} { return [list 1e9 {0 0 1} 0.0] }
    set hmin [expr {$a2/$lmax}]
    set asp [expr {max($lmax/$hmin*0.8660254, $lmax/$lmin)}]
    set inv [expr {1.0/$a2}]
    lassign $c cx cy cz
    return [list $asp [list [expr {$cx*$inv}] [expr {$cy*$inv}] [expr {$cz*$inv}]] [expr {0.5*$a2}]]
}
proc qc::etri {nodes} { variable X; lassign $nodes a b c; tri $X($a) $X($b) $X($c) }
proc qc::easp {e} { variable E; lindex [etri $E($e)] 0 }

# ---------------- load / classify ----------------
proc qc::load {} {
    variable X; variable E; variable NE; variable FN; variable CORNER
    variable newE {}; variable delE {}; variable newN {}; variable vid 0
    array unset X; array unset E; array unset NE; array unset FN; array unset CORNER
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    # element -> CAD surface it was meshed on (used to snap new nodes to the geometry)
    variable SURF
    array unset SURF
    *createmark surfs 1 "all"
    foreach s [hm_getmark surfs 1] {
        *clearmark surfs 1
        *clearmark elems 2
        *createmark surfs 1 $s
        if {![catch {*findmark surfs 1 257 1 elems 0 2}]} {
            foreach e [hm_getmark elems 2] { set SURF($e) $s }
        }
    }
    *clearmark surfs 1
    *clearmark elems 2
    foreach e $eids {
        set nds [hm_getvalue elems id=$e dataname=nodes]
        if {[llength $nds] != 3} { continue }
        set E($e) $nds
        foreach n $nds {
            lappend NE($n) $e
            if {![info exists X($n)]} { set X($n) [lindex [hm_nodevalue $n] 0] }
        }
    }
    return [llength $eids]
}

# feature edges = mesh edges whose two trias meet at more than the feature
# angle, plus any edge that is not shared by exactly two trias.
proc qc::classify {} {
    variable E; variable X; variable FN; variable CORNER; variable COSF
    variable KEEP_SURF_EDGES; variable SURF
    array unset FN; array unset CORNER
    array set ed {}
    foreach e [array names E] {
        lassign $E($e) a b c
        set nrm($e) [lindex [etri $E($e)] 1]
        foreach {u v} [list $a $b $b $c $c $a] { lappend ed([key $u $v]) $e }
    }
    foreach k [array names ed] {
        set el $ed($k)
        set sharp 0
        if {[llength $el] != 2} {
            set sharp 1
        } elseif {[dot $nrm([lindex $el 0]) $nrm([lindex $el 1])] < $COSF} {
            set sharp 1
        } elseif {$KEEP_SURF_EDGES} {
            lassign $el e1 e2
            if {[info exists SURF($e1)] && [info exists SURF($e2)] && $SURF($e1) ne $SURF($e2)} { set sharp 1 }
        }
        if {$sharp} {
            lassign [split $k ","] u v
            lappend FN($u) $v
            lappend FN($v) $u
        }
    }
    # a node on a feature line is a corner unless exactly two feature edges
    # pass through it in a nearly straight line
    foreach n [array names FN] {
        set fl $FN($n)
        if {[llength $fl] != 2} { set CORNER($n) 1; continue }
        lassign $fl p q
        set d1 [sub $X($n) $X($p)]; set d2 [sub $X($q) $X($n)]
        set l1 [len $d1]; set l2 [len $d2]
        if {$l1 < 1e-9 || $l2 < 1e-9} { continue }
        if {[dot $d1 $d2]/($l1*$l2) < 0.94} { set CORNER($n) 1 }   ;# > 20 deg turn
    }
}
proc qc::rank {n} {
    variable FN; variable CORNER
    if {[info exists CORNER($n)]} { return 2 }
    if {[info exists FN($n)]} { return 1 }
    return 0
}
proc qc::is_feat {u v} {
    variable FN
    expr {[info exists FN($u)] && [lsearch -exact $FN($u) $v] >= 0}
}
proc qc::nbrs {n} {
    variable E; variable NE
    set r {}
    foreach e $NE($n) { foreach m $E($e) { if {$m != $n} { lappend r $m } } }
    lsort -unique $r
}
proc qc::edge_elems {u v} {
    variable E; variable NE
    set r {}
    foreach e $NE($u) { if {[lsearch -exact $E($e) $v] >= 0} { lappend r $e } }
    return $r
}

# ---------------- low level mesh edits (in memory) ----------------
proc qc::del_elem {e} {
    variable E; variable NE; variable delE; variable newE; variable SURF
    foreach n $E($e) {
        set i [lsearch -exact $NE($n) $e]
        set NE($n) [lreplace $NE($n) $i $i]
    }
    unset E($e)
    catch { unset SURF($e) }
    set i [lsearch -exact $newE $e]
    if {$i >= 0} { set newE [lreplace $newE $i $i] } else { lappend delE $e }
}
proc qc::add_elem {nodes {surf ""}} {
    variable E; variable NE; variable newE; variable vid; variable SURF
    set e "v[incr vid]"
    set E($e) $nodes
    foreach n $nodes { lappend NE($n) $e }
    if {$surf ne ""} { set SURF($e) $surf }
    lappend newE $e
    return $e
}
proc qc::surf_of {e} {
    variable SURF
    expr {[info exists SURF($e)] ? $SURF($e) : ""}
}
# mid point of u-v, pulled back onto the CAD surface(s) of the given elements
proc qc::snap_mid {u v elems} {
    variable X
    lassign $X($u) ux uy uz; lassign $X($v) vx vy vz
    set p [list [expr {0.5*($ux+$vx)}] [expr {0.5*($uy+$vy)}] [expr {0.5*($uz+$vz)}]]
    return [snap_point $p $elems [expr {0.25*[len [sub $X($u) $X($v)]]}]]
}
# pull point p onto the CAD surface(s) the given elements were meshed on;
# never moves it further than lim (guards against a wrong surface)
proc qc::snap_point {p elems lim} {
    set ss {}
    foreach e $elems { set s [surf_of $e]; if {$s ne "" && [lsearch -exact $ss $s] < 0} { lappend ss $s } }
    if {[llength $ss] == 0} { return $p }
    set q $p
    foreach it {1 2 3} {
        foreach s $ss {
            lassign $q x y z
            if {[catch {hm_findclosestpointonsurface $x $y $z $s} r] || [llength $r] < 3} { return $p }
            set q [lrange $r 0 2]
        }
        if {[llength $ss] == 1} { break }
    }
    if {[len [sub $q $p]] > $lim} { return $p }
    return $q
}

# ---------------- candidate operations ----------------
# every "try_*" returns {} when the operation is not allowed, otherwise
# {worst_aspect_after  script_to_apply}

# collapse node a onto node b
proc qc::try_collapse {a b maxlen} {
    variable X; variable E; variable NE; variable FN
    variable TINY; variable WHY
    set WHY ok
    set dab [dist $a $b]
    if {$dab > $maxlen} { set WHY long; return {} }
    variable LINE_OF
    if {[array size LINE_OF] > 0} {
        # a disappears : it may neither shorten a locked line (b on the same
        # line) nor leave it (b somewhere else)
        if {[info exists LINE_OF($a)]} { set WHY locked; return {} }
    }
    variable KEEP_NODE
    if {[info exists KEEP_NODE($a)]} { set WHY locked; return {} }
    # edges shorter than TINY are features too small to be meshed with the
    # requested size: they may be removed even if they sit on a sharp line
    if {$dab > $TINY} {
        variable LOCK_FEAT_COUNT
        if {$LOCK_FEAT_COUNT && [is_feat $a $b]} { set WHY locked; return {} }
        set ra [rank $a]
        if {$ra == 2} { set WHY corner; return {} }
        if {$ra == 1 && ![is_feat $a $b]} { set WHY offline; return {} }
    }
    set shared [edge_elems $a $b]
    # an edge that is not shared by exactly two trias (T-connection or free
    # edge coming from the CAD) may only be removed when it is tiny
    if {[llength $shared] != 2 && ($dab > $TINY || [llength $shared] < 1)} { set WHY nonmanifold; return {} }
    # link condition: a and b may only share the nodes opposite to edge a-b
    set opp {}
    foreach e $shared { foreach m $E($e) { if {$m != $a && $m != $b} { lappend opp $m } } }
    set opp [lsort -unique $opp]
    set na [nbrs $a]; set nb [nbrs $b]
    set common {}
    foreach m $na { if {[lsearch -exact $nb $m] >= 0} { lappend common $m } }
    if {[lsort -unique $common] ne $opp} { set WHY link; return {} }
    if {$dab > $TINY && ([llength $NE($a)] < 3 || [llength $NE($b)] < 3)} { set WHY valence; return {} }
    # opposite nodes must keep at least 3 trias
    #   (at a tiny non-manifold edge the skin is open anyway: one tria is enough)
    set need [expr {[llength $shared] == 2 ? 3 : 1}]
    foreach m $opp {
        set k 0
        foreach e $shared { if {[lsearch -exact $E($e) $m] >= 0} { incr k } }
        if {[llength $NE($m)] - $k < $need - 1} { set WHY oppvalence; return {} }
    }
    set worst 0.0
    foreach e $NE($a) {
        if {[lsearch -exact $shared $e] >= 0} { continue }
        lassign [etri $E($e)] asp0 n0 ar0
        set nn {}
        foreach m $E($e) { lappend nn [expr {$m == $a ? $b : $m}] }
        lassign [etri $nn] asp1 n1 ar1
        if {$ar1 < 1e-9} { set WHY area; return {} }
        # the normal of a sliver is meaningless, so only well shaped trias are tested
        if {$asp0 < 15.0 && [dot $n0 $n1] < 0.75} { set WHY fold; return {} }
        if {$asp1 > $worst} { set worst $asp1 }
    }
    return [list $worst [list qc::do_collapse $a $b]]
}
proc qc::do_collapse {a b} {
    variable E; variable NE; variable FN; variable CORNER; variable stats
    foreach e [edge_elems $a $b] { del_elem $e }
    foreach e $NE($a) {
        set nn {}
        foreach m $E($e) { lappend nn [expr {$m == $a ? $b : $m}] }
        set s [surf_of $e]
        del_elem $e
        add_elem $nn $s
    }
    if {[info exists FN($a)]} {
        # a was on a feature line : hand its feature edges over to b
        foreach c $FN($a) {
            set i [lsearch -exact $FN($c) $a]
            set FN($c) [lreplace $FN($c) $i $i]
            if {$c != $b && [lsearch -exact $FN($c) $b] < 0} {
                lappend FN($c) $b
                lappend FN($b) $c
            }
        }
        unset FN($a)
        if {[info exists CORNER($a)] || [llength $FN($b)] != 2} { set CORNER($b) 1 }
    }
    unset NE($a)
    incr stats(collapse)
}

# flip the edge u-v shared by two trias
proc qc::try_flip {u v} {
    variable X; variable E
    if {[is_feat $u $v]} { return {} }
    set shared [edge_elems $u $v]
    if {[llength $shared] != 2} { return {} }
    lassign $shared e1 e2
    foreach m $E($e1) { if {$m != $u && $m != $v} { set c $m } }
    foreach m $E($e2) { if {$m != $u && $m != $v} { set d $m } }
    if {$c == $d} { return {} }
    if {[llength [edge_elems $c $d]] > 0} { return {} }
    # keep the orientation of e1 : replace v by d in e1, u by c in e2
    set n1 {}; foreach m $E($e1) { lappend n1 [expr {$m == $v ? $d : $m}] }
    set n2 {}; foreach m $E($e2) { lappend n2 [expr {$m == $u ? $c : $m}] }
    lassign [etri $E($e1)] a1 nr1 ar; lassign [etri $E($e2)] a2 nr2 ar
    lassign [etri $n1] b1 nn1 ar1; lassign [etri $n2] b2 nn2 ar2
    if {$ar1 < 1e-9 || $ar2 < 1e-9} { return {} }
    foreach nn [list $nn1 $nn2] {
        if {[dot $nn $nr1] < 0.75 || [dot $nn $nr2] < 0.75} { return {} }
    }
    return [list [expr {max($b1,$b2)}] [list qc::do_flip $e1 $e2 $n1 $n2]]
}
proc qc::do_flip {e1 e2 n1 n2} {
    variable stats
    set s1 [surf_of $e1]; set s2 [surf_of $e2]
    del_elem $e1; del_elem $e2
    add_elem $n1 $s1; add_elem $n2 $s2
    incr stats(flip)
}

# refine the quad made of tria e and its neighbour across e's longest edge:
# both long sides of the quad get a mid node, the quad becomes 4 trias and
# the two outer neighbours are split in two. This is what a narrow strip
# between two feature lines needs (a single edge split only makes it worse).
proc qc::try_quadsplit {e} {
    variable E
    lassign $E($e) a b c
    set best {}
    foreach {u v} [list $a $b $b $c $c $a] {
        set cand [try_quadsplit_edge $e $u $v]
        if {$cand ne {} && ($best eq {} || [lindex $cand 0] < [lindex $best 0])} { set best $cand }
    }
    return $best
}
# (u,v) must appear in this order inside e -> e = (u,v,c0)
proc qc::try_quadsplit_edge {e u v} {
    variable X; variable E
    if {[is_feat $u $v]} { return {} }
    set shared [edge_elems $u $v]
    if {[llength $shared] != 2} { return {} }
    set e2 [expr {[lindex $shared 0] eq $e ? [lindex $shared 1] : [lindex $shared 0]}]
    foreach m $E($e)  { if {$m != $u && $m != $v} { set c0 $m } }
    foreach m $E($e2) { if {$m != $u && $m != $v} { set d0 $m } }
    if {$c0 == $d0} { return {} }
    # quad, counter-clockwise as seen along the element normals
    set P [list $u $d0 $v $c0]
    set l01 [expr {[dist $u $d0] + [dist $v $c0]}]
    set l12 [expr {[dist $d0 $v] + [dist $c0 $u]}]
    if {$l12 > $l01} { set P [list $d0 $v $c0 $u] }
    lassign $P p0 p1 p2 p3
    if {[feat_locked $p0 $p1] || [feat_locked $p2 $p3]} { return {} }
    # rails p0-p1 and p2-p3 ; each must have exactly one outer neighbour
    set outer {}
    foreach {x y} [list $p0 $p1 $p2 $p3] {
        set o {}
        foreach f [edge_elems $x $y] { if {$f ne $e && $f ne $e2} { lappend o $f } }
        if {[llength $o] != 1} { return {} }
        lappend outer [lindex $o 0]
    }
    lassign $outer o1 o2
    if {$o1 eq $o2} { return {} }
    set in1 [expr {[lsearch -exact $E($e) $p0] >= 0 && [lsearch -exact $E($e) $p1] >= 0 ? $e : $e2}]
    set in2 [expr {$in1 eq $e ? $e2 : $e}]
    set m1 [snap_mid $p0 $p1 [list $in1 $o1]]
    set m2 [snap_mid $p2 $p3 [list $in2 $o2]]
    array set XX [list $p0 $X($p0) $p1 $X($p1) $p2 $X($p2) $p3 $X($p3) M1 $m1 M2 $m2]
    set n_e  [lindex [etri $E($e)] 1]
    set n_e2 [lindex [etri $E($e2)] 1]
    set tris {}
    set worst 0.0
    foreach quad [list [list $p0 M1 M2 $p3] [list M1 $p1 $p2 M2]] {
        lassign $quad q0 q1 q2 q3
        set optA [list [list $q0 $q1 $q2] [list $q0 $q2 $q3]]
        set optB [list [list $q0 $q1 $q3] [list $q1 $q2 $q3]]
        set bw 1e12; set bo {}
        foreach opt [list $optA $optB] {
            set w 0.0; set ok 1
            foreach t $opt {
                lassign $t i j k
                lassign [tri $XX($i) $XX($j) $XX($k)] asp nn ar
                if {$ar < 1e-9 || ([dot $nn $n_e] < 0.6 && [dot $nn $n_e2] < 0.6)} { set ok 0 }
                if {$asp > $w} { set w $asp }
            }
            if {$ok && $w < $bw} { set bw $w; set bo $opt }
        }
        if {$bo eq {}} { return {} }
        if {$bw > $worst} { set worst $bw }
        foreach t $bo { lappend tris $t }
    }
    # outer neighbours after the split
    foreach {o x y mm} [list $o1 $p0 $p1 M1 $o2 $p2 $p3 M2] {
        foreach rep [list $x $y] {
            set pts {}
            foreach m $E($o) { lappend pts [expr {$m == $rep ? $XX($mm) : $X($m)}] }
            set asp [lindex [tri [lindex $pts 0] [lindex $pts 1] [lindex $pts 2]] 0]
            if {$asp > $worst} { set worst $asp }
        }
    }
    return [list $worst [list qc::do_quadsplit $e $e2 $o1 $o2 $P $m1 $m2 $tris]]
}
# refine a needle (A = node opposite the shortest edge B-C): both long edges
# get a mid node -> tip tria (A,M1,M2) + quad (M1,B,C,M2). Repeated, this
# grades the mesh towards an acute corner until the tip is small enough to
# be removed by a tiny-edge collapse.
proc qc::try_needlesplit {e} {
    variable X; variable E; variable SIZE
    lassign $E($e) a b c
    set best 1e12
    foreach {p q r} [list $a $b $c $b $c $a $c $a $b] {
        # p = apex candidate, q-r = opposite edge, order p,q,r keeps orientation
        set d [dist $q $r]
        if {$d < $best} { set best $d; set A $p; set B $q; set C $r }
    }
    if {[dist $A $B] < 0.4*$SIZE || [dist $A $C] < 0.4*$SIZE} { return {} }
    if {[feat_locked $A $B] || [feat_locked $C $A]} { return {} }
    set outer {}
    foreach {x y} [list $A $B $C $A] {
        set o {}
        foreach f [edge_elems $x $y] { if {$f ne $e} { lappend o $f } }
        if {[llength $o] != 1} { return {} }
        lappend outer [lindex $o 0]
    }
    lassign $outer o1 o2
    if {$o1 eq $o2} { return {} }
    set m1 [snap_mid $A $B [list $e $o1]]
    set m2 [snap_mid $C $A [list $e $o2]]
    array set XX [list $A $X($A) $B $X($B) $C $X($C) M1 $m1 M2 $m2]
    set n_e [lindex [etri $E($e)] 1]
    set tris [list [list $A M1 M2]]
    set worst [lindex [tri $XX($A) $m1 $m2] 0]
    set bw 1e12; set bo {}
    foreach opt [list [list [list M1 $B $C] [list M1 $C M2]] [list [list M1 $B M2] [list $B $C M2]]] {
        set w 0.0; set ok 1
        foreach t $opt {
            lassign $t i j k
            lassign [tri $XX($i) $XX($j) $XX($k)] asp nn ar
            if {$ar < 1e-9 || [dot $nn $n_e] < 0.6} { set ok 0 }
            if {$asp > $w} { set w $asp }
        }
        if {$ok && $w < $bw} { set bw $w; set bo $opt }
    }
    if {$bo eq {}} { return {} }
    if {$bw > $worst} { set worst $bw }
    foreach t $bo { lappend tris $t }
    foreach {o x y mm} [list $o1 $A $B M1 $o2 $C $A M2] {
        foreach rep [list $x $y] {
            set pts {}
            foreach m $E($o) { lappend pts [expr {$m == $rep ? $XX($mm) : $X($m)}] }
            set asp [lindex [tri [lindex $pts 0] [lindex $pts 1] [lindex $pts 2]] 0]
            if {$asp > $worst} { set worst $asp }
        }
    }
    return [list $worst [list qc::do_needlesplit $e $o1 $o2 $A $B $C $m1 $m2 $tris]]
}
proc qc::do_needlesplit {e o1 o2 A B C m1 m2 tris} {
    variable E; variable stats
    set M1 [new_node $m1]; set M2 [new_node $m2]
    set s [surf_of $e]
    del_elem $e
    foreach t $tris {
        set nn {}
        foreach q $t { lappend nn [expr {$q eq "M1" ? $M1 : ($q eq "M2" ? $M2 : $q)}] }
        add_elem $nn $s
    }
    foreach {o x y m} [list $o1 $A $B $M1 $o2 $C $A $M2] {
        set nds $E($o); set so [surf_of $o]
        del_elem $o
        set n1 {}; foreach q $nds { lappend n1 [expr {$q == $y ? $m : $q}] }
        set n2 {}; foreach q $nds { lappend n2 [expr {$q == $x ? $m : $q}] }
        add_elem $n1 $so; add_elem $n2 $so
        feat_insert $x $y $m
    }
    incr stats(needlesplit)
}
proc qc::new_node {xyz} {
    variable X; variable NE; variable newN; variable vid
    set m "n[incr vid]"
    set X($m) $xyz
    set NE($m) {}
    lappend newN $m
    return $m
}
proc qc::feat_insert {u v m} {
    variable FN
    if {![is_feat $u $v]} { return }
    set i [lsearch -exact $FN($u) $v]; set FN($u) [lreplace $FN($u) $i $i $m]
    set i [lsearch -exact $FN($v) $u]; set FN($v) [lreplace $FN($v) $i $i $m]
    set FN($m) [list $u $v]
}
proc qc::do_quadsplit {e e2 o1 o2 P m1 m2 tris} {
    variable E; variable stats
    lassign $P p0 p1 p2 p3
    set M1 [new_node $m1]; set M2 [new_node $m2]
    set s [surf_of $e]
    del_elem $e; del_elem $e2
    foreach t $tris {
        set nn {}
        foreach q $t { lappend nn [expr {$q eq "M1" ? $M1 : ($q eq "M2" ? $M2 : $q)}] }
        add_elem $nn $s
    }
    foreach {o x y m} [list $o1 $p0 $p1 $M1 $o2 $p2 $p3 $M2] {
        set nds $E($o); set so [surf_of $o]
        del_elem $o
        set n1 {}; foreach q $nds { lappend n1 [expr {$q == $y ? $m : $q}] }
        set n2 {}; foreach q $nds { lappend n2 [expr {$q == $x ? $m : $q}] }
        add_elem $n1 $so; add_elem $n2 $so
        feat_insert $x $y $m
    }
    incr stats(quadsplit)
}

# split the edge u-v at its mid point
# LINE_OF(node) : CAD lines whose node count must not change (set by the caller,
# e.g. the two opposite edges of a fillet). An edge is locked when both of its
# nodes lie on the same such line.
proc qc::feat_locked {u v} {
    variable LOCK_FEAT_COUNT; variable LINE_OF
    if {$LOCK_FEAT_COUNT && [is_feat $u $v]} { return 1 }
    if {[info exists LINE_OF($u)] && [info exists LINE_OF($v)]} {
        foreach l $LINE_OF($u) { if {[lsearch -exact $LINE_OF($v) $l] >= 0} { return 1 } }
    }
    # first / last segment of a locked line : one interior node + the end
    # corner, joined by a feature edge (the line itself)
    if {([info exists LINE_OF($u)] || [info exists LINE_OF($v)]) && [is_feat $u $v]} { return 1 }
    return 0
}
proc qc::try_split {u v {apex ""}} {
    variable X; variable E
    if {[feat_locked $u $v]} { return {} }
    set shared [edge_elems $u $v]
    if {[llength $shared] != 2} { return {} }
    if {$apex eq ""} {
        set pm [snap_mid $u $v $shared]
    } else {
        # split where the apex of a flat ("cap") tria projects onto u-v, so that
        # the apex can afterwards be merged into the new node
        set d [sub $X($v) $X($u)]
        set t [expr {[dot [sub $X($apex) $X($u)] $d] / [dot $d $d]}]
        set t [expr {max(0.15, min(0.85, $t))}]
        lassign $X($u) ux uy uz; lassign $d dx dy dz
        set pm [snap_point [list [expr {$ux+$t*$dx}] [expr {$uy+$t*$dy}] [expr {$uz+$t*$dz}]] $shared [expr {0.25*[len $d]}]]
    }
    set worst 0.0
    foreach e $shared {
        foreach rep [list $u $v] {
            set p {}
            foreach m $E($e) { lappend p [expr {$m == $rep ? $pm : $X($m)}] }
            set asp [lindex [tri [lindex $p 0] [lindex $p 1] [lindex $p 2]] 0]
            if {$asp > $worst} { set worst $asp }
        }
    }
    return [list $worst [list qc::do_split $u $v $pm]]
}
proc qc::do_split {u v pm} {
    variable E; variable stats
    set m [new_node $pm]
    foreach e [edge_elems $u $v] {
        set nds $E($e); set s [surf_of $e]
        del_elem $e
        set n1 {}; foreach q $nds { lappend n1 [expr {$q == $v ? $m : $q}] }
        set n2 {}; foreach q $nds { lappend n2 [expr {$q == $u ? $m : $q}] }
        add_elem $n1 $s; add_elem $n2 $s
    }
    feat_insert $u $v $m
    incr stats(split)
    return $m
}
# split edge u-v at the given point (already on the CAD line) when no tria
# gets above LIM; returns the new node or "" (edge not manifold / too bad)
proc qc::split_at_point {u v pm} {
    variable E; variable LIM
    set shared [edge_elems $u $v]
    if {[llength $shared] != 2} { return "" }
    foreach e $shared {
        foreach rep [list $u $v] {
            set p {}
            foreach m $E($e) { lappend p [expr {$m == $rep ? $pm : [set ::qc::X($m)]}] }
            lassign [tri {*}$p] asp nn ar
            if {$ar < 1e-12 || $asp > $LIM} { return "" }
        }
    }
    return [do_split $u $v $pm]
}
# arcs : list of {p_start p_end {interior points in order}}. For every arc
# whose mesh edge runs straight from the start to the end node, the interior
# points are inserted one after another (stops at the first one that would
# create a tria above lim). Returns the number of inserted nodes.
proc qc::restore_arcs {arcs lim} {
    variable X; variable LIM; variable stats
    set LIM [expr {$lim * 0.97}]
    array set stats {collapse 0 flip 0 split 0 quadsplit 0 needlesplit 0 spec 0 dblface 0}
    if {[load] == 0} { return 0 }
    classify
    set n 0
    variable restore_why {}
    foreach arc $arcs {
        lassign $arc p0 p1 mids
        set u [nearest_node $p0]; set v [nearest_node $p1]
        if {$u eq "" || $v eq "" || $u eq $v} { lappend restore_why "no end nodes"; continue }
        if {[len [sub $X($u) $p0]] > 1e-3 || [len [sub $X($v) $p1]] > 1e-3} {
            lappend restore_why [format "end nodes off the line (%.4g / %.4g)" [len [sub $X($u) $p0]] [len [sub $X($v) $p1]]]
            continue
        }
        set k 0
        foreach pm $mids {
            set m [split_at_point $u $v $pm]
            if {$m eq ""} {
                lappend restore_why "split $k refused (edge $u-$v: [llength [edge_elems $u $v]] trias, worst [format %.2f [local_worst [list $u $v]]])"
                break
            }
            incr n; incr k
            set u $m
        }
    }
    commit
    return $n
}
# V8 : closest point of polyline pts to p -> {distance arc_position point}
proc qc::poly_project {p pts} {
    set best 1e30; set bs 0.0; set bp $p
    set s0 0.0
    set a [lindex $pts 0]
    foreach b [lrange $pts 1 end] {
        set d [sub $b $a]
        set dd [dot $d $d]
        set t 0.0
        if {$dd > 1e-24} { set t [expr {max(0.0, min(1.0, [dot [sub $p $a] $d] / $dd))}] }
        lassign $a ax ay az; lassign $d dx dy dz
        set q [list [expr {$ax+$t*$dx}] [expr {$ay+$t*$dy}] [expr {$az+$t*$dz}]]
        set dist [len [sub $q $p]]
        if {$dist < $best} { set best $dist; set bs [expr {$s0 + $t*sqrt($dd)}]; set bp $q }
        set s0 [expr {$s0 + sqrt($dd)}]
        set a $b
    }
    return [list $best $bs $bp]
}
# V8 : matched band edges that ended with different node counts. jobs : list of
# {polyline_points n_add} for the line with FEWER nodes. Its longest mesh edges
# are split on the CAD line (split_at_point : only when no tria gets above lim)
# until it has the count of its partner again -> top and bottom match, no tear.
# Returns the number of inserted nodes.
proc qc::equalize_lines {jobs lim} {
    variable X; variable NE; variable LIM; variable stats
    set LIM [expr {$lim * 0.97}]
    array set stats {collapse 0 flip 0 split 0 quadsplit 0 needlesplit 0 spec 0 dblface 0}
    if {[load] == 0} { return 0 }
    classify
    set n 0
    foreach job $jobs {
        lassign $job pts add
        if {[llength $pts] < 2 || $add <= 0} { continue }
        set total 0.0
        set a [lindex $pts 0]
        foreach b [lrange $pts 1 end] { set total [expr {$total + [len [sub $b $a]]}]; set a $b }
        set tol [expr {max(1e-4, min(0.01, 0.002 * $total))}]
        set x0 1e30; set y0 1e30; set z0 1e30; set x1 -1e30; set y1 -1e30; set z1 -1e30
        foreach q $pts {
            lassign $q qx qy qz
            set x0 [expr {min($x0,$qx)}]; set y0 [expr {min($y0,$qy)}]; set z0 [expr {min($z0,$qz)}]
            set x1 [expr {max($x1,$qx)}]; set y1 [expr {max($y1,$qy)}]; set z1 [expr {max($z1,$qz)}]
        }
        # mesh nodes on the line, ordered along it
        set on {}
        foreach nd [array names NE] {
            if {[llength $NE($nd)] == 0} { continue }
            lassign $X($nd) px py pz
            if {$px < $x0-$tol || $px > $x1+$tol || $py < $y0-$tol || $py > $y1+$tol || $pz < $z0-$tol || $pz > $z1+$tol} { continue }
            lassign [poly_project $X($nd) $pts] dist s
            if {$dist < $tol} { lappend on [list $nd $s] }
        }
        set on [lsort -real -index 1 $on]
        for {set k 0} {$k < $add} {incr k} {
            # mesh edges along the line, longest first
            set segs {}
            for {set i 0} {$i < [llength $on] - 1} {incr i} {
                set u [lindex $on $i 0]; set v [lindex $on [expr {$i+1}] 0]
                if {[llength [edge_elems $u $v]] != 2} { continue }
                lappend segs [list $i [dist $u $v]]
            }
            set done 0
            foreach sg [lsort -real -decreasing -index 1 $segs] {
                set i [lindex $sg 0]
                set u [lindex $on $i 0]; set v [lindex $on [expr {$i+1}] 0]
                set su [lindex $on $i 1]; set sv [lindex $on [expr {$i+1}] 1]
                # point of the CAD line halfway between the two nodes
                lassign $X($u) ux uy uz; lassign $X($v) vx vy vz
                set mid [list [expr {0.5*($ux+$vx)}] [expr {0.5*($uy+$vy)}] [expr {0.5*($uz+$vz)}]]
                set pm [lindex [poly_project $mid $pts] 2]
                set m [split_at_point $u $v $pm]
                if {$m eq ""} { continue }
                set on [linsert $on [expr {$i+1}] [list $m [expr {0.5*($su+$sv)}]]]
                incr n
                set done 1
                break
            }
            if {!$done} { break }
        }
    }
    commit
    return $n
}
proc qc::nearest_node {p} {
    variable X; variable NE
    set best ""; set bd 1e30
    foreach nd [array names NE] {
        if {[llength $NE($nd)] == 0} { continue }
        set d [len [sub $X($nd) $p]]
        if {$d < $bd} { set bd $d; set best $nd }
    }
    return $best
}

# worst aspect of the trias currently touching any of the given nodes
proc qc::local_worst {nodes} {
    variable E; variable NE
    set w 0.0
    foreach n $nodes {
        foreach e $NE($n) {
            set a [easp $e]
            if {$a > $w} { set w $a }
        }
    }
    return $w
}

# try to repair one bad tria; returns 1 when something was changed
proc qc::fix_elem {e allow_split} {
    variable E; variable X; variable LIM; variable SIZE
    lassign $E($e) a b c
    set before [easp $e]
    set best {}
    set bestv $before
    set maxlen [expr {0.75*$SIZE}]
    set edges [list [list $a $b] [list $b $c] [list $c $a]]
    foreach uv $edges {
        lassign $uv u v
        foreach cand [list [try_collapse $u $v $maxlen] [try_collapse $v $u $maxlen] [try_flip $u $v]] {
            if {$cand eq {}} { continue }
            if {[lindex $cand 0] < $bestv - 1e-6} { set bestv [lindex $cand 0]; set best [lindex $cand 1] }
        }
    }
    if {$allow_split && $bestv > $LIM} {
        # longest edge of the element
        set lu {}; set ll -1
        foreach uv $edges {
            lassign $uv u v
            set d [dist $u $v]
            if {$d > $ll} { set ll $d; set lu $uv }
        }
        foreach cand [list [try_split [lindex $lu 0] [lindex $lu 1]] [try_quadsplit $e]] {
            if {$cand ne {} && [lindex $cand 0] < $bestv - 1e-6} { set bestv [lindex $cand 0]; set best [lindex $cand 1] }
        }
    }
    if {$best eq {} && $allow_split >= 2} {
        # last resort: grade the mesh towards an acute corner. The tip keeps
        # its shape (same aspect) but gets small enough for a tiny-edge collapse.
        set cand [try_needlesplit $e]
        if {$cand ne {} && [lindex $cand 0] <= $before * 1.02} { set best [lindex $cand 1] }
    }
    if {$best eq {}} { return 0 }
    eval $best
    return 1
}

# Two trias on the same three nodes are a zero-thickness "double face" (the CAD
# has two surfaces lying on top of each other, or a fin collapsed onto itself).
# They enclose no volume, so both are removed - but only where every edge stays
# shared by two trias afterwards.
proc qc::remove_double_faces {} {
    variable E; variable stats
    array set byk {}
    foreach e [array names E] { lappend byk([join [lsort $E($e)] ,]) $e }
    set n 0
    foreach k [array names byk] {
        if {[llength $byk($k)] != 2} { continue }
        lassign $byk($k) e1 e2
        if {![info exists E($e1)] || ![info exists E($e2)]} { continue }
        lassign $E($e1) a b c
        set ok 1
        foreach {u v} [list $a $b $b $c $c $a] {
            set cnt [llength [edge_elems $u $v]]
            if {$cnt != 2 && $cnt != 4} { set ok 0 }
        }
        if {!$ok} { continue }
        del_elem $e1; del_elem $e2
        incr n
    }
    incr stats(dblface) $n
    return $n
}

# ---------------- speculative repair ----------------
# Some elements cannot be repaired by one improving step (two narrow strips
# side by side, a flat tria wedged between feature lines, ...). Here a refining
# step is applied even though it makes things worse at first, the elements it
# created are repaired in turn, and the whole attempt is kept only if nothing
# above the limit is left among them. Otherwise the mesh is rolled back.
proc qc::snapshot {} {
    variable X; variable E; variable NE; variable FN; variable CORNER; variable SURF
    variable newE; variable delE; variable newN; variable vid; variable stats
    set s {}
    foreach v {X E NE FN CORNER SURF stats} { lappend s [array get $v] }
    lappend s $newE $delE $newN $vid
    return $s
}
proc qc::restore {s} {
    variable X; variable E; variable NE; variable FN; variable CORNER; variable SURF
    variable newE; variable delE; variable newN; variable vid; variable stats
    set i 0
    foreach v {X E NE FN CORNER SURF stats} { array unset $v; array set $v [lindex $s $i]; incr i }
    lassign [lrange $s $i end] newE delE newN vid
}
# elements created after element counter vid0 that are still above the limit, worst first
proc qc::created_bad {vid0} {
    variable E; variable newE; variable LIM
    set r {}
    foreach e $newE {
        if {[string range $e 1 end] <= $vid0 || ![info exists E($e)]} { continue }
        set a [easp $e]
        if {$a > $LIM} { lappend r [list $e $a] }
    }
    set out {}
    foreach p [lsort -real -decreasing -index 1 $r] { lappend out [lindex $p 0] }
    return $out
}
# refining candidates for element e, best (lowest resulting aspect) first
proc qc::refine_cands {e} {
    variable E; variable SIZE
    lassign $E($e) a b c
    set ll -1
    foreach {u v w} [list $a $b $c $b $c $a $c $a $b] {
        set d [dist $u $v]
        if {$d > $ll} { set ll $d; set lu $u; set lv $v; set apex $w }
    }
    if {$ll < 0.2*$SIZE} { return {} }
    set r {}
    foreach cand [list [try_quadsplit $e] [try_split $lu $lv $apex] [try_split $lu $lv] [try_needlesplit $e]] {
        if {$cand ne {}} { lappend r $cand }
    }
    return [lsort -real -index 0 $r]
}
proc qc::spec_fix {e} {
    variable E; variable vid
    foreach cand [refine_cands $e] {
        set snap [snapshot]
        set vid0 $vid
        eval [lindex $cand 1]
        set budget 8
        set ok 0
        for {set it 0} {$it < 40} {incr it} {
            set bad [created_bad $vid0]
            if {[llength $bad] == 0} { set ok 1; break }
            set progressed 0
            foreach b $bad {
                if {![info exists E($b)]} { continue }
                if {[fix_elem $b 1]} { set progressed 1 }
            }
            if {$progressed} { continue }
            if {[incr budget -1] < 0} { break }
            set c2 [lindex [refine_cands [lindex $bad 0]] 0]
            if {$c2 eq {}} { break }
            eval [lindex $c2 1]
        }
        if {$ok} { return 1 }
        restore $snap
    }
    return 0
}

proc qc::bad_list {} {
    variable E; variable LIM
    set r {}
    foreach e [array names E] {
        set a [easp $e]
        if {$a > $LIM} { lappend r [list $e $a] }
    }
    # worst first
    set out {}
    foreach p [lsort -real -decreasing -index 1 $r] { lappend out [lindex $p 0] }
    return $out
}

# one line of text about an element (for the log of elements that stay bad)
proc qc::describe {e} {
    variable E
    lassign $E($e) a b c
    set t {}
    foreach {u v} [list $a $b $b $c $c $a] {
        lappend t [format "%.2f%s" [dist $u $v] [expr {[is_feat $u $v] ? "F" : ""}]]
    }
    variable SIZE
    set ops {}
    foreach {u v} [list $a $b $b $c $c $a] {
        foreach {nm cand} [list col [try_collapse $u $v [expr {0.75*$SIZE}]] colr [try_collapse $v $u [expr {0.75*$SIZE}]] \
                flip [try_flip $u $v] split [try_split $u $v] quad [try_quadsplit_edge $e $u $v]] {
            variable WHY
            lappend ops "$nm=[expr {$cand eq {} ? ([string match col* $nm] ? $WHY : {-}) : [format %.1f [lindex $cand 0]]}]"
        }
    }
    set cand [try_needlesplit $e]
    lappend ops "needle=[expr {$cand eq {} ? {-} : [format %.1f [lindex $cand 0]]}]"
    return [format "asp=%.2f edges={%s} ranks={%d %d %d} ops: %s" [easp $e] $t [rank $a] [rank $b] [rank $c] $ops]
}

# ---------------- write the changes back to HyperMesh ----------------
proc qc::commit {} {
    variable X; variable E; variable newE; variable delE; variable newN
    if {[llength $delE] == 0 && [llength $newE] == 0} { return }
    # component of the elements that are going to be replaced
    set comp ""
    if {[llength $delE] > 0} {
        catch { set comp [hm_getvalue elems id=[lindex $delE 0] dataname=collector.name] }
    }
    if {$comp ne ""} { catch { *currentcollector components $comp } }
    array set real {}
    variable NE
    foreach m $newN {
        if {![info exists NE($m)] || [llength $NE($m)] == 0} { continue }
        lassign $X($m) x y z
        *createnode $x $y $z 0 0 0
        set real($m) [hm_latestentityid nodes]
    }
    foreach e $newE {
        set ids {}
        foreach n $E($e) { lappend ids [expr {[info exists real($n)] ? $real($n) : $n}] }
        *createlist nodes 1 {*}$ids
        *createelement 103 1 1 1
    }
    if {[llength $delE] > 0} {
        *clearmark elems 1
        eval *createmark elems 1 $delE
        *deletemark elems 1
        *clearmark elems 1
    }
    catch { *nodecleartempmark }
}

# ---------------- driver ----------------
proc qc::run {lim size {feature_deg 25.0}} {
    variable LIM; variable SIZE; variable COSF; variable stats; variable E
    # work with a small safety margin below the requested limit
    set LIM [expr {$lim * 0.97}]
    variable TINY
    set SIZE $size
    set TINY [expr {0.15 * $size}]
    set COSF [expr {cos($feature_deg * acos(-1.0) / 180.0)}]
    array set stats {collapse 0 flip 0 split 0 quadsplit 0 needlesplit 0 spec 0 dblface 0}
    set n0 [load]
    classify
    set bad0 [llength [bad_list]]
    for {set pass 1} {$pass <= 60} {incr pass} {
        set bad [bad_list]
        if {[llength $bad] == 0} { break }
        set changed 0
        # splitting is the last resort: only once collapse / flip are exhausted
        foreach allow_split {0 1 2} {
            foreach e $bad {
                if {![info exists E($e)]} { continue }
                if {[easp $e] <= $LIM} { continue }
                if {[fix_elem $e $allow_split]} { incr changed }
            }
            if {$changed > 0} { break }
        }
        if {$changed == 0 && [remove_double_faces] > 0} { incr changed }
        if {$changed == 0} {
            # nothing improves in one step any more -> speculative repair
            foreach e $bad {
                if {![info exists E($e)] || [easp $e] <= $LIM} { continue }
                if {[spec_fix $e]} { incr changed; incr stats(spec) }
            }
        }
        if {$changed == 0} { break }
    }
    variable stuck {}
    set bl [bad_list]
    set left [llength $bl]
    foreach e [lrange $bl 0 19] { lappend stuck [describe $e] }
    commit
    return [list n_before $n0 bad_before $bad0 bad_after $left passes $pass \
        collapse $stats(collapse) flip $stats(flip) split $stats(split) quadsplit $stats(quadsplit) needlesplit $stats(needlesplit) spec $stats(spec) dblface $stats(dblface)]
}
'@

# ----- YL_mesh_stats.tcl -----
$YLEngineFiles['YL_mesh_stats.tcl'] = @'
# =====================================================================
# YL_mesh_stats.tcl : mesh quality report of .hm files (hand mesh vs auto mesh)
#   hmbatch.exe -tcl YL_mesh_stats.tcl
#   YL_STATS_LIST : UTF-8 text file, one .hm path per line
#   YL_STATS_OUT  : output text file (report), a .csv with the same stem is also written
#
# The comparison is made on the same basis for every model:
#   skin  = the tria/quad shell elements of the model; for a tetra-only model the
#           outer faces of the tetras (*findfaces) - i.e. what the tetra mesher saw
#   tetra = tetra4 / tetra10 elements
# Metrics (computed here from the node coordinates, independent of the HM version)
#   tria  : edge length, min / max angle, aspect (longest edge / shortest height,
#           HyperMesh definition), equilateral quality q = 4*sqrt(3)*A / sum(l^2)
#           (1 = equilateral), node valence (ideal 6 inside a face)
#   tetra : aspect (longest edge / shortest height), collapse (HyperMesh: height
#           to the opposite face / sqrt(face area) normalised to 1 for the regular
#           tetra), dihedral angles min / max
# =====================================================================
proc ys_env {n d} { if {[info exists ::env($n)] && $::env($n) ne ""} { return $::env($n) }; return $d }
set ys_list [ys_env YL_STATS_LIST "models.txt"]
set ys_out  [ys_env YL_STATS_OUT  "mesh_stats.txt"]
set ys_max_tet [ys_env YL_STATS_MAX_TET 400000]


proc ys_log {t} { if {[info exists ::OUT]} { puts $::OUT $t; flush $::OUT } }

proc ys_count {type {how all} args} {
    *clearmark $type 1
    if {$how eq "all"} { *createmark $type 1 all } else { eval *createmark $type 1 [list $how] $args }
    set n [llength [hm_getmark $type 1]]
    *clearmark $type 1
    return $n
}
proc ys_elems_by_config {cfgs} {
    *clearmark elems 1
    catch {eval *createmark elems 1 {"by config"} $cfgs}
    set r [hm_getmark elems 1]
    *clearmark elems 1
    return $r
}
proc ys_pct {k n} { if {$n <= 0} { return 0.0 }; return [expr {100.0 * $k / $n}] }
proc ys_quant {sorted p} {
    set n [llength $sorted]
    if {$n == 0} { return 0.0 }
    set i [expr {int(floor($p * ($n - 1)))}]
    return [lindex $sorted $i]
}
proc ys_sub {a b} { list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}] }
proc ys_dot {a b} { expr {[lindex $a 0]*[lindex $b 0]+[lindex $a 1]*[lindex $b 1]+[lindex $a 2]*[lindex $b 2]} }
proc ys_cross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc ys_len {a} { expr {sqrt([ys_dot $a $a])} }

proc ys_xyz {nd} {
    if {![info exists ::YX($nd)]} { set ::YX($nd) [lindex [hm_nodevalue $nd] 0] }
    return $::YX($nd)
}

# ---------------- tria skin ----------------
proc ys_tria_stats {elist} {
    set R [dict create n 0]
    if {[llength $elist] == 0} { return $R }
    set lens {}; set asps {}; set qs {}; set angmin_all {}
    set n_lt20 0; set n_lt30 0; set n_gt120 0; set n_a3 0; set n_a5 0; set n_q05 0
    set angmax_p {}
    array unset VAL; array set VAL {}
    array unset ESZ; array set ESZ {}
    array unset EDGE_ELEMS; array set EDGE_ELEMS {}
    set rad [expr {180.0 / acos(-1.0)}]
    set n 0
    foreach e $elist {
        set nds [lrange [hm_nodelist $e] 0 2]
        if {[llength $nds] < 3} { continue }
        lassign $nds n1 n2 n3
        set p1 [ys_xyz $n1]; set p2 [ys_xyz $n2]; set p3 [ys_xyz $n3]
        set v12 [ys_sub $p2 $p1]; set v23 [ys_sub $p3 $p2]; set v31 [ys_sub $p1 $p3]
        set a [ys_len $v12]; set b [ys_len $v23]; set c [ys_len $v31]
        if {$a < 1e-12 || $b < 1e-12 || $c < 1e-12} { continue }
        set A [expr {0.5 * [ys_len [ys_cross $v12 [ys_sub $p3 $p1]]]}]
        set lmax [expr {max($a,$b,$c)}]
        set asp [expr {$A > 1e-14 ? $lmax / (2.0 * $A / $lmax) : 1e6}]
        # HyperMesh "2D aspect" of a tria (longest edge / shortest height) is
        # normalised so that the equilateral tria is 1 : h = sqrt(3)/2 * l
        set asp [expr {$asp * sqrt(3.0) / 2.0}]
        set q [expr {4.0 * sqrt(3.0) * $A / ($a*$a + $b*$b + $c*$c)}]
        # angles
        set A1 [expr {acos(max(-1.0,min(1.0,($a*$a+$c*$c-$b*$b)/(2.0*$a*$c)))) * $rad}]
        set A2 [expr {acos(max(-1.0,min(1.0,($a*$a+$b*$b-$c*$c)/(2.0*$a*$b)))) * $rad}]
        set A3 [expr {180.0 - $A1 - $A2}]
        set amin [expr {min($A1,$A2,$A3)}]
        set amax [expr {max($A1,$A2,$A3)}]
        lappend lens $a $b $c
        lappend asps $asp
        lappend qs $q
        lappend angmin_all $amin
        lappend angmax_p $amax
        if {$amin < 20} { incr n_lt20 }
        if {$amin < 30} { incr n_lt30 }
        if {$amax > 120} { incr n_gt120 }
        if {$asp > 3} { incr n_a3 }
        if {$asp > 5} { incr n_a5 }
        if {$q < 0.5} { incr n_q05 }
        foreach nd $nds { if {[info exists VAL($nd)]} { incr VAL($nd) } else { set VAL($nd) 1 } }
        set ESZ($e) [expr {($a+$b+$c)/3.0}]
        set ::YT($e) [list $p1 $p2 $p3]
        foreach {i j} [list $n1 $n2 $n2 $n3 $n3 $n1] {
            set k [expr {$i < $j ? "$i,$j" : "$j,$i"}]
            lappend EDGE_ELEMS($k) $e
        }
        incr n
    }
    # size jump between neighbours (ratio of mean edge length, gradation)
    set worst 1.0
    set jumps {}
    foreach k [array names EDGE_ELEMS] {
        set es $EDGE_ELEMS($k)
        if {[llength $es] != 2} { continue }
        lassign $es x y
        set r [expr {max($ESZ($x),$ESZ($y)) / max(1e-12,min($ESZ($x),$ESZ($y)))}]
        lappend jumps $r
        if {$r > $worst} { set worst $r }
    }
    set jumps [lsort -real $jumps]
    # curvature capture : angle between the normals of the two trias of an edge.
    # Sharp CAD edges are ~90 deg; 20..60 deg means a curved face (fillet,
    # cylinder) faceted with too few rows.
    set k_smooth 0; set k_mid 0; set k_sharp 0; set k_all 0
    foreach k [array names EDGE_ELEMS] {
        set es $EDGE_ELEMS($k)
        if {[llength $es] != 2} { continue }
        set nn {}
        foreach x $es {
            lassign $::YT($x) q1 q2 q3
            lappend nn [ys_cross [ys_sub $q2 $q1] [ys_sub $q3 $q1]]
        }
        set la [ys_len [lindex $nn 0]]; set lb [ys_len [lindex $nn 1]]
        if {$la < 1e-14 || $lb < 1e-14} { continue }
        set c [expr {abs([ys_dot [lindex $nn 0] [lindex $nn 1]]) / ($la * $lb)}]
        set ang [expr {acos(min(1.0, $c)) * $rad}]
        incr k_all
        if {$ang < 20} { incr k_smooth } elseif {$ang < 60} { incr k_mid } else { incr k_sharp }
    }
    dict set R pct_edge_facet [ys_pct $k_mid $k_all]
    dict set R pct_edge_sharp [ys_pct $k_sharp $k_all]
    array unset ::YT
    # valence of interior skin nodes (closed skin : every node)
    set v6 0; set v4 0; set v8 0; set nv 0
    foreach nd [array names VAL] {
        incr nv
        set v $VAL($nd)
        if {$v == 6} { incr v6 }
        if {$v <= 4} { incr v4 }
        if {$v >= 8} { incr v8 }
    }
    set lens [lsort -real $lens]
    set asps [lsort -real $asps]
    set qs   [lsort -real $qs]
    set angmin_all [lsort -real $angmin_all]
    set angmax_p [lsort -real $angmax_p]
    set sum 0.0; foreach l $lens { set sum [expr {$sum+$l}] }
    set mean [expr {$sum / [llength $lens]}]
    set ss 0.0; foreach l $lens { set ss [expr {$ss+($l-$mean)*($l-$mean)}] }
    set cv [expr {sqrt($ss / [llength $lens]) / $mean}]
    set sa 0.0; foreach x $asps { set sa [expr {$sa+$x}] }
    set sq 0.0; foreach x $qs { set sq [expr {$sq+$x}] }
    dict set R n $n
    dict set R len_min [lindex $lens 0]
    dict set R len_p05 [ys_quant $lens 0.05]
    dict set R len_mean $mean
    dict set R len_p95 [ys_quant $lens 0.95]
    dict set R len_max [lindex $lens end]
    dict set R len_cv $cv
    dict set R ang_min [lindex $angmin_all 0]
    dict set R ang_min_p05 [ys_quant $angmin_all 0.05]
    dict set R ang_max_p99 [ys_quant $angmax_p 0.99]
    dict set R pct_ang_lt20 [ys_pct $n_lt20 $n]
    dict set R pct_ang_lt30 [ys_pct $n_lt30 $n]
    dict set R pct_ang_gt120 [ys_pct $n_gt120 $n]
    dict set R asp_mean [expr {$sa / $n}]
    dict set R asp_p95 [ys_quant $asps 0.95]
    dict set R asp_max [lindex $asps end]
    dict set R pct_asp_gt3 [ys_pct $n_a3 $n]
    dict set R pct_asp_gt5 [ys_pct $n_a5 $n]
    dict set R q_mean [expr {$sq / $n}]
    dict set R q_p05 [ys_quant $qs 0.05]
    dict set R pct_q_lt05 [ys_pct $n_q05 $n]
    dict set R pct_val6 [ys_pct $v6 $nv]
    dict set R pct_val_le4 [ys_pct $v4 $nv]
    dict set R pct_val_ge8 [ys_pct $v8 $nv]
    dict set R jump_p95 [ys_quant $jumps 0.95]
    dict set R jump_max $worst
    # shape class by the largest angle : near equilateral (< 75), right-angled
    # (80..100, typical of split quads / mapped faces), obtuse (> 100)
    set k_eq 0; set k_rt 0; set k_ob 0
    foreach x $angmax_p {
        if {$x < 75} { incr k_eq } elseif {$x >= 80 && $x <= 100} { incr k_rt } elseif {$x > 100} { incr k_ob }
    }
    dict set R pct_equi [ys_pct $k_eq $n]
    dict set R pct_right [ys_pct $k_rt $n]
    dict set R pct_obtuse [ys_pct $k_ob $n]
    # histogram of the equilateral quality
    set h {}
    foreach lim {0.2 0.4 0.6 0.8 0.9 1.01} {
        set k 0
        foreach x $qs { if {$x < $lim} { incr k } }
        lappend h [format "<%.1f:%.1f%%" $lim [ys_pct $k $n]]
    }
    dict set R q_cum $h
    return $R
}

# ---------------- tetra ----------------
proc ys_tet_stats {elist maxn} {
    set R [dict create n [llength $elist]]
    if {[llength $elist] == 0} { return $R }
    set step 1
    if {[llength $elist] > $maxn} { set step [expr {int(ceil(double([llength $elist]) / $maxn))}] }
    set rad [expr {180.0 / acos(-1.0)}]
    set asps {}; set cols {}; set dmin_all {}
    set n 0; set a5 0; set a10 0; set c15 0; set c30 0; set d10 0; set d165 0
    set i 0
    foreach e $elist {
        if {$i % $step} { incr i; continue }
        incr i
        set nds [lrange [hm_nodelist $e] 0 3]
        if {[llength $nds] < 4} { continue }
        set P {}
        foreach nd $nds { lappend P [ys_xyz $nd] }
        lassign $P p0 p1 p2 p3
        set V [expr {abs([ys_dot [ys_sub $p1 $p0] [ys_cross [ys_sub $p2 $p0] [ys_sub $p3 $p0]]]) / 6.0}]
        # faces opposite each vertex
        set faces [list [list $p1 $p2 $p3 $p0] [list $p0 $p2 $p3 $p1] [list $p0 $p1 $p3 $p2] [list $p0 $p1 $p2 $p3]]
        set lmax 0.0
        foreach {x y} {0 1 0 2 0 3 1 2 1 3 2 3} {
            set l [ys_len [ys_sub [lindex $P $x] [lindex $P $y]]]
            if {$l > $lmax} { set lmax $l }
        }
        set hmin 1e30; set colmin 1e30
        set nrms {}
        foreach f $faces {
            lassign $f a b c opp
            set cr [ys_cross [ys_sub $b $a] [ys_sub $c $a]]
            set fa [expr {0.5 * [ys_len $cr]}]
            if {$fa < 1e-14} { set hmin 0.0; set colmin 0.0; lappend nrms {0 0 0}; continue }
            set h [expr {3.0 * $V / $fa}]
            if {$h < $hmin} { set hmin $h }
            # collapse : h / sqrt(face area), regular tetra = 1  (h/sqrt(A) = 1.2408)
            set col [expr {$h / sqrt($fa) / 1.2408064788027995}]
            if {$col < $colmin} { set colmin $col }
            # outward normal
            set nn [ys_cross [ys_sub $b $a] [ys_sub $c $a]]
            if {[ys_dot $nn [ys_sub $opp $a]] > 0} { set nn [list [expr {-[lindex $nn 0]}] [expr {-[lindex $nn 1]}] [expr {-[lindex $nn 2]}]] }
            set ln [ys_len $nn]
            lappend nrms [list [expr {[lindex $nn 0]/$ln}] [expr {[lindex $nn 1]/$ln}] [expr {[lindex $nn 2]/$ln}]]
        }
        # aspect : longest edge / shortest height, regular tetra = 1 (h = sqrt(2/3) l)
        set asp [expr {$hmin > 1e-14 ? $lmax / $hmin * sqrt(2.0/3.0) : 1e6}]
        # dihedral angles between the 6 face pairs : pi - angle between outward normals
        set dmin 180.0; set dmax 0.0
        foreach {x y} {0 1 0 2 0 3 1 2 1 3 2 3} {
            set c [ys_dot [lindex $nrms $x] [lindex $nrms $y]]
            set d [expr {180.0 - acos(max(-1.0,min(1.0,$c))) * $rad}]
            if {$d < $dmin} { set dmin $d }
            if {$d > $dmax} { set dmax $d }
        }
        lappend asps $asp; lappend cols $colmin; lappend dmin_all $dmin
        incr n
        if {$asp > 5} { incr a5 }
        if {$asp > 10} { incr a10 }
        if {$colmin < 0.15} { incr c15 }
        if {$colmin < 0.3} { incr c30 }
        if {$dmin < 10} { incr d10 }
        if {$dmax > 165} { incr d165 }
    }
    set asps [lsort -real $asps]; set cols [lsort -real $cols]; set dmin_all [lsort -real $dmin_all]
    set sa 0.0; foreach x $asps { set sa [expr {$sa+$x}] }
    set sc 0.0; foreach x $cols { set sc [expr {$sc+$x}] }
    dict set R sampled $n
    dict set R asp_mean [expr {$sa/$n}]
    dict set R asp_p95 [ys_quant $asps 0.95]
    dict set R asp_max [lindex $asps end]
    dict set R pct_asp_gt5 [ys_pct $a5 $n]
    dict set R pct_asp_gt10 [ys_pct $a10 $n]
    dict set R col_mean [expr {$sc/$n}]
    dict set R col_min [lindex $cols 0]
    dict set R pct_col_lt015 [ys_pct $c15 $n]
    dict set R pct_col_lt03 [ys_pct $c30 $n]
    dict set R dih_min [lindex $dmin_all 0]
    dict set R pct_dih_lt10 [ys_pct $d10 $n]
    dict set R pct_dih_gt165 [ys_pct $d165 $n]
    return $R
}

proc ys_g {d k {fmt %.3f}} { if {[dict exists $d $k]} { return [format $fmt [dict get $d $k]] }; return "" }

# ---------------- main ----------------
# library mode : another script sources this file for the procs only
#   (set ::YS_LIB_ONLY 1 before "source")
if {[info exists ::YS_LIB_ONLY] && $::YS_LIB_ONLY} { return }

set OUT [open $ys_out w]
fconfigure $OUT -encoding utf-8
set CSV [open "[file rootname $ys_out].csv" w]
fconfigure $CSV -encoding utf-8
puts $CSV "model,comps,surfs,nodes,n_tria,n_quad,n_tet4,n_tet10,skin_src,len_min,len_mean,len_max,len_cv,ang_min,ang_max_p,pct_ang_lt20,pct_ang_lt30,pct_ang_gt120,asp_mean,asp_p95,asp_max,pct_asp_gt3,pct_asp_gt5,q_mean,q_p05,pct_q_lt05,pct_val6,pct_val_le4,pct_val_ge8,size_ratio_max_nb,jump_p95,pct_edge_facet,pct_right,pct_obtuse,tet_asp_mean,tet_asp_p95,tet_asp_max,pct_tet_asp_gt5,pct_tet_asp_gt10,tet_col_mean,pct_tet_col_lt015,pct_tet_col_lt03,tet_dih_min,pct_tet_dih_lt10,pct_tet_dih_gt165"

set models {}
if {![catch {open $ys_list r} fh]} {
    fconfigure $fh -encoding utf-8
    foreach line [split [read $fh] "\n"] {
        set line [string trim [string trimleft $line "﻿"] " \r\n\""]
        if {$line eq "" || [string index $line 0] eq "#"} { continue }
        lappend models [file normalize $line]
    }
    close $fh
}
ys_log "YL mesh stats  [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]   models: [llength $models]"

foreach m $models {
    set t0 [clock seconds]
    ys_log ""
    ys_log "=== [file tail $m]"
    catch { hm_answernext yes }
    catch { *deletemodel }
    array unset ::YX
    if {[catch { catch { hm_answernext yes }; *readfile $m } err]} { ys_log "  READ FAILED: $err"; continue }
    set ncomp [ys_count comps]; set nsurf [ys_count surfs]; set nnode [ys_count nodes]
    set tri [ys_elems_by_config tria3]; set tri6 [ys_elems_by_config tria6]
    set quad [ys_elems_by_config {quad4 quad8}]
    set t4 [ys_elems_by_config tetra4]; set t10 [ys_elems_by_config tetra10]
    set other [ys_count elems]
    ys_log [format "  comps %d  surfs %d  nodes %d  elems %d : tria3 %d tria6 %d quad %d tetra4 %d tetra10 %d" $ncomp $nsurf $nnode $other [llength $tri] [llength $tri6] [llength $quad] [llength $t4] [llength $t10]]
    *clearmark comps 1; *createmark comps 1 all
    set cn {}
    foreach c [hm_getmark comps 1] {
        *clearmark elems 1; catch { *createmark elems 1 "by comp id" $c }
        lappend cn "[hm_getvalue comps id=$c dataname=name]([llength [hm_getmark elems 1]])"
        *clearmark elems 1
    }
    *clearmark comps 1
    ys_log "  components: [join $cn {, }]"
    # tetra first (before the skin faces are created)
    set TS [ys_tet_stats [concat $t4 $t10] $ys_max_tet]
    # skin
    set skin_src "shell"
    set skin [concat $tri $tri6]
    if {[llength $t4] + [llength $t10] > 0} {
        set skin_src "tet faces"
        *clearmark comps 1; *createmark comps 1 all
        catch { *findfaces comps 1 }
        *clearmark comps 1
        *clearmark elems 1
        catch { *createmark elems 1 "by comp name" "^faces" }
        set skin [hm_getmark elems 1]
        *clearmark elems 1
    }
    set SS [ys_tria_stats $skin]
    # optional dump of the skin for pictures (YL_STATS_DUMP = folder)
    set dump_dir [ys_env YL_STATS_DUMP ""]
    if {$dump_dir ne "" && [llength $skin] > 0} {
        file mkdir $dump_dir
        set fd [open [file join $dump_dir "[file rootname [file tail $m]]_skin.txt"] w]
        array unset seenN; array set seenN {}
        set tl {}
        foreach e $skin {
            set nds [lrange [hm_nodelist $e] 0 2]
            if {[llength $nds] < 3} { continue }
            foreach nd $nds { set seenN($nd) 1 }
            lappend tl "T [join $nds { }]"
        }
        foreach nd [array names seenN] { puts $fd "N $nd [join [ys_xyz $nd] { }]" }
        puts $fd [join $tl "\n"]
        close $fd
    }
    ys_log [format "  SKIN (%s) trias %d" $skin_src [dict get $SS n]]
    if {[dict get $SS n] > 0} {
        ys_log [format "    edge length  min %.3f  p05 %.3f  mean %.3f  p95 %.3f  max %.3f  CV %.2f" [dict get $SS len_min] [dict get $SS len_p05] [dict get $SS len_mean] [dict get $SS len_p95] [dict get $SS len_max] [dict get $SS len_cv]]
        ys_log [format "    min angle    worst %.1f  p05 %.1f   <20deg %.2f%%  <30deg %.2f%%   max angle p99 %.1f  >120deg %.2f%%" [dict get $SS ang_min] [dict get $SS ang_min_p05] [dict get $SS pct_ang_lt20] [dict get $SS pct_ang_lt30] [dict get $SS ang_max_p99] [dict get $SS pct_ang_gt120]]
        ys_log [format "    aspect       mean %.2f  p95 %.2f  max %.2f   >3 %.2f%%  >5 %.2f%%" [dict get $SS asp_mean] [dict get $SS asp_p95] [dict get $SS asp_max] [dict get $SS pct_asp_gt3] [dict get $SS pct_asp_gt5]]
        ys_log [format "    quality q    mean %.3f  p05 %.3f  q<0.5 %.2f%%   cumulative %s" [dict get $SS q_mean] [dict get $SS q_p05] [dict get $SS pct_q_lt05] [dict get $SS q_cum]]
        ys_log [format "    valence      =6 %.1f%%  <=4 %.1f%%  >=8 %.1f%%" [dict get $SS pct_val6] [dict get $SS pct_val_le4] [dict get $SS pct_val_ge8]]
        ys_log [format "    shape        max angle <75 (equilateral) %.1f%%  80-100 (right) %.1f%%  >100 (obtuse) %.1f%%" [dict get $SS pct_equi] [dict get $SS pct_right] [dict get $SS pct_obtuse]]
        ys_log [format "    size jump between neighbours  p95 %.2f  max %.2f" [dict get $SS jump_p95] [dict get $SS jump_max]]
        ys_log [format "    curvature    edges with 20-60 deg normal turn (faceted curve) %.2f%%   >60 deg (sharp) %.2f%%" [dict get $SS pct_edge_facet] [dict get $SS pct_edge_sharp]]
    }
    if {[dict get $TS n] > 0} {
        ys_log [format "  TETRA %d (sampled %d)" [dict get $TS n] [dict get $TS sampled]]
        ys_log [format "    aspect   mean %.2f  p95 %.2f  max %.1f   >5 %.2f%%  >10 %.3f%%" [dict get $TS asp_mean] [dict get $TS asp_p95] [dict get $TS asp_max] [dict get $TS pct_asp_gt5] [dict get $TS pct_asp_gt10]]
        ys_log [format "    collapse mean %.3f  min %.3f   <0.15 %.3f%%  <0.3 %.2f%%" [dict get $TS col_mean] [dict get $TS col_min] [dict get $TS pct_col_lt015] [dict get $TS pct_col_lt03]]
        ys_log [format "    dihedral min %.1f   <10deg %.3f%%  >165deg %.3f%%" [dict get $TS dih_min] [dict get $TS pct_dih_lt10] [dict get $TS pct_dih_gt165]]
    }
    ys_log "  ([expr {[clock seconds] - $t0}] s)"
    puts $CSV [join [list "\"[file tail $m]\"" $ncomp $nsurf $nnode [llength $tri] [llength $quad] [llength $t4] [llength $t10] $skin_src \
        [ys_g $SS len_min] [ys_g $SS len_mean] [ys_g $SS len_max] [ys_g $SS len_cv] [ys_g $SS ang_min %.2f] [ys_g $SS ang_max_p99 %.2f] \
        [ys_g $SS pct_ang_lt20 %.2f] [ys_g $SS pct_ang_lt30 %.2f] [ys_g $SS pct_ang_gt120 %.2f] [ys_g $SS asp_mean] [ys_g $SS asp_p95] [ys_g $SS asp_max] \
        [ys_g $SS pct_asp_gt3 %.2f] [ys_g $SS pct_asp_gt5 %.2f] [ys_g $SS q_mean] [ys_g $SS q_p05] [ys_g $SS pct_q_lt05 %.2f] \
        [ys_g $SS pct_val6 %.1f] [ys_g $SS pct_val_le4 %.1f] [ys_g $SS pct_val_ge8 %.1f] [ys_g $SS jump_max %.2f] [ys_g $SS jump_p95 %.2f] [ys_g $SS pct_edge_facet %.2f] [ys_g $SS pct_right %.1f] [ys_g $SS pct_obtuse %.1f] \
        [ys_g $TS asp_mean] [ys_g $TS asp_p95] [ys_g $TS asp_max] [ys_g $TS pct_asp_gt5 %.2f] [ys_g $TS pct_asp_gt10 %.3f] \
        [ys_g $TS col_mean] [ys_g $TS pct_col_lt015 %.3f] [ys_g $TS pct_col_lt03 %.2f] [ys_g $TS dih_min %.2f] [ys_g $TS pct_dih_lt10 %.3f] [ys_g $TS pct_dih_gt165 %.3f]] ","]
    flush $CSV
}
ys_log ""
ys_log "DONE"
close $CSV
close $OUT
exit 0
'@

# ----- YL_V8_batch.tcl -----
$YLEngineFiles['YL_V8_batch.tcl'] = @'
# =====================================================================
# YL_V8 : Ansys-style 2D surface mesh (R-Trias, aspect <= limit, 0 failures)
#         -> 3D tetra is RUN ONLY when the 2D skin has passed.
#
#   2D (Ansys Mechanical surface-mesh procedure mapped to HyperMesh 2019)
#     1. Defeature       : edges / strip surfaces smaller than the defeature
#                          size are suppressed (Ansys "Defeature size" /
#                          "Virtual Topology"). Tiny ones unconditionally,
#                          short ones only when tangent (< SHARP_ANGLE).
#     2. Edge sizing     : every CAD line gets an explicit division count from
#                          the proximity size (narrow surface -> local size,
#                          min 0.3 x size) and its curvature. On curved bands
#                          (fillets, cylinders) the two opposite long edges get
#                          the SAME count (Ansys "Edge sizing" + "Mapped face"):
#                          top and bottom match.
#
#   V8 changes against V7 (YL_V7_batch.tcl is kept unchanged for comparison)
#     - fillet rule : interior nodes across a fillet = 1 (narrow) / 2 (medium) /
#       3 (wide) by arc length (FILLET_MEDIUM / FILLET_WIDE x size), never more
#       than FILLET_MAX_NODES (V7: one row per 22.5 deg -> far too dense).
#       Fillet arc = turn FILLET_MIN_TURN..FILLET_MAX_TURN and radius <= FILLET_MAX_RADIUS x size.
#       Other arcs (holes, gentle bends, large curves) : size, at most ARC_ANGLE_LONG per element.
#       Fillet cross-section counts are locked in the clean-up like the band edges.
#     - band matching : the drift limit of a chain is BAND_DRIFT (V7 1.5), the two
#       curved short sides of a 4-sided fillet band are matched as well.
#     - clean-up locks : curve nodes are released first, the matched band edges
#       (top / bottom node count) only when that is not enough (stage 2+).
#     - equalize : a band whose edges still differ after the clean-up gets the
#       missing nodes inserted on its shorter-count edge (qc::equalize_lines).
#     - results : fillet_max_nodes, band_unpaired, engine.
#     3. Surface mesh    : R-Trias (advancing front), all surfaces in one
#                          interactive session (*set_meshedgeparams/*automesh);
#                          falls back to proximity size buckets if that fails.
#     4. Smoothing       : quality gated Laplacian smoothing of interior nodes,
#                          nodes snapped back to the CAD surface, feature lines
#                          and corners are never moved (Ansys "Smoothing High").
#     5. Improve         : YL_qclean.tcl (collapse / flip / split) as the last
#                          safety net, same as YL_V6.
#     6. Check           : HyperMesh aspect test, free edges, T-edges
#                          -> pass2d = 1 only when all three are 0.
#   3D  : DO_TETRA = 1 and pass2d = 1  ->  *tetmesh + Abaqus element type.
#         The 2D skin is never modified or retried here.
#
# Batch : run_batch.tcl sets ::YL_BATCH 1 and the ::YL_* values.
# GUI   : "run" this file directly -> asks for the size and the surfaces.
# =====================================================================
if {![info exists ::YL_BATCH]} { set ::YL_BATCH 0 }
set ::YL_RESULT [list status "FAIL" msg "not finished"]

# ---------------- settings ----------------
set DO_TETRA          0          ;# 0 : 2D only,  1 : 2D + 3D tetra (only when 2D passed)
set ABAQUS_ELEM_TYPE  "C3D10H"   ;# tetra4 / tetra10 type of the Abaqus template (C3D4, C3D4H, C3D10H ...)
set ELEM_SIZE         2.0
set TARGET_ASPECT     5.0        ;# no 2D element may exceed this
set SHARP_ANGLE       25.0       ;# feature angle that must be preserved (deg)
set DEFEATURE_TINY    0.40       ;# x size : removed whatever the angle (Ansys defeature size)
set DEFEATURE_SHORT   0.75       ;# x size : removed when tangent only
set MIN_SIZE_FACTOR   0.30       ;# x size : smallest local element size (proximity)
set SIZE_GROWTH       1.40       ;# ratio between two size buckets
set SMOOTH_PASSES     4
set MAX_ARC_ANGLE     22.5       ;# deg : (V7 rule, only kept for the messages) - V8 uses the fillet rule below
# V8 fillet rule (how an analyst meshes a fillet by hand): interior nodes across
# a fillet = 1 (narrow), 2 (medium), 3 (wide), never more
set FILLET_MAX_NODES  3          ;# interior nodes across a fillet, upper limit
set FILLET_MEDIUM     1.5        ;# x size : arc length above this -> 2 interior nodes
set FILLET_WIDE       3.0        ;# x size : arc length above this -> 3 interior nodes
set FILLET_MIN_TURN   30.0       ;# deg : a fillet arc turns at least this (less : gentle bend -> size rule)
set FILLET_MAX_TURN   120.0      ;# deg : arcs turning up to this are fillet cross-sections / corner arcs
set FILLET_MAX_RADIUS 3.0        ;# x size : larger radius = large curved face, not a fillet -> size rule
set ARC_ANGLE_LONG    30.0       ;# deg : other curved lines (holes, long arcs) : at most this turn per element
set BAND_DRIFT        2.0        ;# a chain of matched band edges may span this factor of wished counts
set STRIP_ELONGATION  3.0        ;# narrow strip of width w : element size along it = min(size, this x w)
set CURVED_TURN       10.0      ;# deg : a line turning more than this is "curved" (fillet / cylinder edge) and is never defeatured
set KEEP_SURF_EDGES   1          ;# 1 : CAD surface boundaries (also tangent fillet edges) are feature lines
set LOCK_FEAT_COUNT   0          ;# 1 : clean-up may not add / remove nodes on CAD lines (tested: leaves aspect failures)
set DELETE_2D_SKIN    1
set TET_GROWTH        1.2
set MIN_TET_COLLAPSE  0.15
set OUT_DIR           "C:/work/mesh"
set BASE_NAME         "YL_V8"

if {[info exists ::YL_DO_TETRA]}  { set DO_TETRA $::YL_DO_TETRA }
if {[info exists ::YL_ELEM_TYPE]} { set ABAQUS_ELEM_TYPE $::YL_ELEM_TYPE }
if {[info exists ::YL_ASPECT]}    { set TARGET_ASPECT $::YL_ASPECT }
if {[info exists ::YL_OUT_DIR]}   { set OUT_DIR $::YL_OUT_DIR }
if {[info exists ::YL_BASE_NAME]} { set BASE_NAME $::YL_BASE_NAME }
# optional overrides (tests / troubleshooting) : 0 0 switches defeature off
if {[info exists ::YL_DEFEATURE_TINY]}  { set DEFEATURE_TINY  $::YL_DEFEATURE_TINY }
if {[info exists ::YL_DEFEATURE_SHORT]} { set DEFEATURE_SHORT $::YL_DEFEATURE_SHORT }
if {$::YL_BATCH} {
    if {[info exists ::YL_ELEM_SIZE] && $::YL_ELEM_SIZE > 0.05} { set ELEM_SIZE [expr {double($::YL_ELEM_SIZE)}] }
} elseif {![catch {hm_getfloat "Enter Target R-Trias Mesh Size (mm):" "Please enter target element size" $ELEM_SIZE} user_val]} {
    if {$user_val ne "" && $user_val > 0.05} { set ELEM_SIZE [expr {double($user_val)}] }
}
set ABAQUS_ELEM_TYPE [string toupper [string trim $ABAQUS_ELEM_TYPE]]
file mkdir $OUT_DIR

set yl8_DIR [file dirname [file normalize [info script]]]
source [file join $yl8_DIR "YL_V8_qclean.tcl"]

# ---------------- helpers (same checks as YL_V6) ----------------
proc yl8_count {cfgs} {
    *clearmark elems 1
    catch {eval *createmark elems 1 {"by config"} $cfgs}
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}
# number of trias HyperMesh itself reports above the aspect limit
proc yl8_bad_aspect {lim} {
    *clearmark elems 1
    *clearmark elems 2
    *createmark elems 1 "by config" tria3
    set r -1
    if {[llength [hm_getmark elems 1]] == 0} { return 0 }
    if {![catch {*elementtestaspect elems 1 $lim 2 2 0 "2D Aspect Ratio"}]} { set r [llength [hm_getmark elems 2]] }
    *clearmark elems 1
    *clearmark elems 2
    return $r
}
# mode 0 : free edges, mode 1 : T-connections
proc yl8_edges {mode} {
    catch {*createmark comps 1 "^edges"; *deletemark comps 1}
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    set n -1
    if {![catch {*findedges1 elems 1 $mode}]} {
        *clearmark elems 2
        *createmark elems 2 "by comp name" "^edges"
        set n [llength [hm_getmark elems 2]]
        *clearmark elems 2
    }
    catch {*createmark comps 1 "^edges"; *deletemark comps 1}
    *clearmark elems 1
    return $n
}
# tetra mesh from the tria skin (parameters in the form the Tetramesh panel records)
proc yl8_tetmesh {growth} {
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    set err ""
    if {[catch {
        *createstringarray 2 "pars: upd_shell fix_comp_bdr pre_cln post_cln" "tet: 291 $growth -1 0 0.8 0"
        *tetmesh elems 1 1 elems 0 -1 1 2
    } err]} { set err "tetmesh error: $err" } else { set err "" }
    *clearmark elems 1
    return $err
}
proc yl8_delete_elems {cfgs} {
    *clearmark elems 1
    catch {eval *createmark elems 1 {"by config"} $cfgs}
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
    *clearmark elems 1
}

# ---------------- geometry queries ----------------
proc yl8_line_length {l} {
    set len 0.0
    catch { set len [hm_linelength $l] }
    return $len
}
# edges that bound at least one surface. V8 : taken from hm_getsurfaceedges
# (same list the mesher uses) - a suppressed (defeatured) line still reports
# its surfaces through hm_getsurfacesfromedge, but the mesh runs across it,
# so it was counted as an "arc with one element".
proc yl8_live_lines {} {
    *clearmark surfs 1
    *createmark surfs 1 all
    array set seen {}
    set r {}
    foreach s [hm_getmark surfs 1] {
        foreach l [yl8_surf_edges $s] {
            if {![info exists seen($l)]} { set seen($l) 1; lappend r $l }
        }
    }
    *clearmark surfs 1
    return $r
}
proc yl8_surf_edges {s} {
    set eds {}
    catch { set eds [join [hm_getsurfaceedges $s]] }
    return $eds
}
# width of a surface = area / longest edge (a strip of width w and length L -> w)
proc yl8_surf_width {s} {
    set a 0.0
    catch { set a [hm_getareaofsurface surfs $s] }
    if {$a <= 1e-9} { return 0.0 }
    set lmax 0.0
    foreach l [yl8_surf_edges $s] {
        set ll [yl8_line_length $l]
        if {$ll > $lmax} { set lmax $ll }
    }
    if {$lmax <= 1e-6} { return 0.0 }
    return [expr {$a / $lmax}]
}
proc yl8_surf_elem_count {s} {
    *clearmark surfs 1
    *clearmark elems 2
    *createmark surfs 1 $s
    set n 0
    if {![catch {*findmark surfs 1 257 1 elems 0 2}]} { set n [llength [hm_getmark elems 2]] }
    *clearmark surfs 1
    *clearmark elems 2
    return $n
}

# average element size of the skin = edge of the equilateral tria with the
# mean tria area (total CAD surface area / number of trias)
proc yl8_avg_elem_size {n_tria} {
    if {$n_tria <= 0} { return 0.0 }
    *clearmark surfs 1
    *createmark surfs 1 all
    set area 0.0
    foreach s [hm_getmark surfs 1] {
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        set area [expr {$area + $a}]
    }
    *clearmark surfs 1
    return [expr {sqrt(4.0 / sqrt(3.0) * $area / $n_tria)}]
}

# ---------------- 1. defeature (Ansys defeature size / virtual topology) ----------------
# *edgesmarksuppress mark break_angle : suppresses the marked edges whose
# surface-surface angle is below break_angle (HyperMesh "toggle" with angle)
proc yl8_surf_count {} {
    *clearmark surfs 1
    *createmark surfs 1 all
    set n [llength [hm_getmark surfs 1]]
    *clearmark surfs 1
    return $n
}
# returns the number of surfaces merged away (one per suppressed edge between
# two surfaces; hm_getsurfacesfromedge still answers for suppressed edges, so
# the line count cannot be used)
proc yl8_suppress {lines break_angle} {
    if {[llength $lines] == 0} { return 0 }
    set before [yl8_surf_count]
    *clearmark lines 1
    eval *createmark lines 1 $lines
    catch { *edgesmarksuppress 1 $break_angle }
    *clearmark lines 1
    return [expr {$before - [yl8_surf_count]}]
}
# turning angle (deg) of a line, cached (geometry does not change while defeaturing)
proc yl8_line_turn {l} {
    if {![info exists ::yl8_turn_cache($l)]} { set ::yl8_turn_cache($l) [lindex [yl8_line_info $l] 3] }
    return $::yl8_turn_cache($l)
}
# Curvature is preserved: fillet / cylinder surfaces are tangent to their
# neighbours, so a pure "tangent + short / narrow" rule merged every small
# fillet into the flat faces and the mesh then cut straight across the arc.
# Curved edges and strips with a curved edge are therefore never suppressed.
proc yl8_defeature {size sharp_deg tiny_f short_f} {
    array unset ::yl8_turn_cache
    set tiny  [expr {$tiny_f  * $size}]
    set short [expr {$short_f * $size}]
    set keep_min [expr {$::MIN_SIZE_FACTOR * $size}]
    set n 0
    # a) short edges (straight ones only)
    set tiny_edges {}
    set short_edges {}
    foreach l [yl8_live_lines] {
        set ll [yl8_line_length $l]
        if {$ll <= 1e-9} { continue }
        if {$ll >= $short} { continue }
        # curved edges are kept - unless shorter than the minimum element
        # size: those cannot be meshed anyway and only produce slivers
        if {$ll >= $keep_min && [yl8_line_turn $l] > $::CURVED_TURN} { continue }
        if {$ll < $tiny} { lappend tiny_edges $l } else { lappend short_edges $l }
    }
    incr n [yl8_suppress $tiny_edges 179.0]
    incr n [yl8_suppress $short_edges $sharp_deg]
    # b) narrow strip surfaces (chamfers / slivers): all of their edges
    *clearmark surfs 1
    *createmark surfs 1 all
    set tiny_strip {}
    set short_strip {}
    foreach s [hm_getmark surfs 1] {
        set w [yl8_surf_width $s]
        if {$w <= 1e-9 || $w >= $short} { continue }
        # curved strip (fillet / cylinder band) : keep it, unless it is
        # narrower than the minimum element size (sliver, not meshable)
        set curved 0
        if {$w >= $keep_min} {
            foreach l [yl8_surf_edges $s] { if {[yl8_line_turn $l] > $::CURVED_TURN} { set curved 1; break } }
        }
        if {$curved} { continue }
        if {$w < $tiny} {
            foreach l [yl8_surf_edges $s] { lappend tiny_strip $l }
        } elseif {$w < $short} {
            foreach l [yl8_surf_edges $s] { lappend short_strip $l }
        }
    }
    *clearmark surfs 1
    incr n [yl8_suppress [lsort -unique -integer $tiny_strip] 179.0]
    incr n [yl8_suppress [lsort -unique -integer $short_strip] $sharp_deg]
    return $n
}

# ---------------- 2+3. edge sizing + matched opposite edges + R-Trias mesh ----------------
# Ansys "Edge Sizing" + "Mapped Face" idea: every CAD line gets an explicit
# number of divisions, and on strip-like surfaces (fillets, cylinders, chamfers)
# the two long opposite edges get the SAME number, so the curved band has the
# same node count at the top and at the bottom. All surfaces are meshed in one
# interactive session so that the counts are honoured everywhere.
#
# Edge index of *set_meshedgeparams (measured on HyperMesh 2019 hmbatch):
#   surfaces in ascending id order, their edges in hm_getsurfaceedges order,
#   every line counted once.  *automesh <face_index> meshes one face.

# length, end points and turning angle (deg) of a line
proc yl8_line_info {l} {
    set len [yl8_line_length $l]
    set ts {0.0 0.125 0.25 0.375 0.5 0.625 0.75 0.875 1.0}
    set pts {}
    catch { set pts [hm_getcoordinatesofpointsonline $l $ts] }
    if {[llength $pts] < 9} { return [list $len {} {} 0.0] }
    set turn 0.0
    for {set i 1} {$i < 8} {incr i} {
        set d1 [qc::sub [lindex $pts $i] [lindex $pts [expr {$i-1}]]]
        set d2 [qc::sub [lindex $pts [expr {$i+1}]] [lindex $pts $i]]
        set l1 [qc::len $d1]; set l2 [qc::len $d2]
        if {$l1 < 1e-9 || $l2 < 1e-9} { continue }
        set c [expr {[qc::dot $d1 $d2] / ($l1*$l2)}]
        set c [expr {max(-1.0, min(1.0, $c))}]
        set turn [expr {$turn + acos($c) * 180.0 / acos(-1.0)}]
    }
    return [list $len [lindex $pts 0] [lindex $pts end] $turn]
}
proc yl8_ends_touch {a b tol} {
    foreach p $a { foreach q $b {
        if {$p eq {} || $q eq {}} { continue }
        if {[qc::len [qc::sub $p $q]] < $tol} { return 1 }
    } }
    return 0
}
# is a curved line a fillet cross-section / short corner arc ?
proc yl8_is_fillet_arc {len turn size} {
    if {$turn <= $::CURVED_TURN || $turn < $::FILLET_MIN_TURN || $turn > $::FILLET_MAX_TURN} { return 0 }
    set radius [expr {$len / ($turn * acos(-1.0) / 180.0)}]
    return [expr {$radius <= $::FILLET_MAX_RADIUS * $size}]
}
# target number of element edges (divisions) on a curved line, 0 = not curved.
#   fillet arc : 1 / 2 / 3 interior nodes by arc length (narrow / medium / wide),
#                never more than FILLET_MAX_NODES, never below the minimum size
#   other arc  : element size, at most ARC_ANGLE_LONG per element
proc yl8_curve_divs {len turn size min_size} {
    if {$turn <= $::CURVED_TURN} { return 0 }
    set nmax [expr {max(1, int(floor($len / $min_size)))}]
    if {[yl8_is_fillet_arc $len $turn $size]} {
        set nodes 1
        if {$len > $::FILLET_MEDIUM * $size} { set nodes 2 }
        if {$len > $::FILLET_WIDE   * $size} { set nodes 3 }
        set nodes [expr {min($nodes, $::FILLET_MAX_NODES)}]
        return [expr {max(1, min($nodes + 1, $nmax))}]
    }
    set n  [expr {max(1, int(round($len / $size)))}]
    set nc [expr {int(ceil($turn / $::ARC_ANGLE_LONG))}]
    return [expr {max($n, min($nc, $nmax))}]
}
# union-find helpers for the groups of lines that must share one count
proc yl8_uf_find {v} {
    upvar 1 UF uf
    while {$uf($v) != $v} { set uf($v) $uf($uf($v)); set v $uf($v) }
    return $v
}

# returns list {n_matched_groups n_unmeshed}; the matched pairs are kept in ::yl8_pairs
proc yl8_mesh_surfaces {surf_ids size min_f growth} {
    set ::yl8_pairs {}
    set ::yl8_fillet_lines {}
    set min_size [expr {$min_f * $size}]
    set sids [lsort -integer -unique $surf_ids]
    # edge order of the meshing session + line data
    set order {}
    array set SE {}
    array set LI {}
    foreach s $sids {
        set SE($s) {}
        foreach l [yl8_surf_edges $s] {
            if {[lsearch -exact $SE($s) $l] < 0} { lappend SE($s) $l }
            if {![info exists LI($l)]} {
                lappend order $l
                set LI($l) [yl8_line_info $l]
            }
        }
    }
    # local size per surface : the requested element size everywhere (same
    # edge division as a plain HyperMesh automesh: length / size), so a face
    # has the same density on all of its sides. Only a strip much narrower
    # than the size is reduced, and only as far as needed to keep its trias
    # at an elongation of STRIP_ELONGATION (not down to its width).
    # Curvature never shrinks a whole surface: it only sets the division
    # count of the curved line itself (rows across a fillet), see below.
    array set H {}
    foreach s $sids {
        set w [yl8_surf_width $s]
        set H($s) $size
        if {$w > 1e-9} { set H($s) [expr {max($min_size, min($size, $::STRIP_ELONGATION * $w))}] }
    }
    array set LH {}
    foreach s $sids {
        foreach l $SE($s) {
            if {![info exists LH($l)] || $H($s) < $LH($l)} { set LH($l) $H($s) }
        }
    }
    # divisions per line : size; curved lines by the V8 fillet rule
    # (yl8_curve_divs : 1 / 2 / 3 nodes across a fillet, not one row per 22.5 deg)
    array set N {}
    array set UF {}
    array set FIX {}
    foreach l $order {
        lassign $LI($l) len p0 p1 turn
        set n [expr {max(1, int(round($len / $LH($l))))}]
        set nc [yl8_curve_divs $len $turn $size $min_size]
        if {$nc > 0} {
            if {[yl8_is_fillet_arc $len $turn $size]} {
                # fillet cross-section : the rule is the count (not a minimum)
                set n $nc
                set FIX($l) 1
            } else {
                set n [expr {max($n, $nc)}]
            }
        }
        set N($l) $n
        set UF($l) $l
    }
    set ::yl8_unpaired 0
    # strip-like surfaces : the two longest edges are opposite (no common end
    # point), of similar length, and every other edge is shorter than both
    foreach s $sids {
        set eds $SE($s)
        if {[llength $eds] < 2} { continue }
        set byl {}
        foreach l $eds { lappend byl [list $l [lindex $LI($l) 0]] }
        set byl [lsort -real -decreasing -index 1 $byl]
        lassign [lindex $byl 0] a la
        lassign [lindex $byl 1] b lb
        if {$lb <= 1e-9 || $la / $lb > 1.6} { continue }
        if {[llength $byl] > 2 && [lindex $byl 2 1] >= 0.999 * $lb} { continue }
        set tol [expr {max(1e-3, 0.02 * $lb)}]
        if {[yl8_ends_touch [lrange $LI($a) 1 2] [lrange $LI($b) 1 2] $tol]} { continue }
        # only curved bands: a fillet (the short side edges are arcs) or a
        # cylinder / torus band (both long edges are arcs). Flat faces are free.
        set side_turn 0.0
        foreach p [lrange $byl 2 end] { set side_turn [expr {max($side_turn, [lindex $LI([lindex $p 0]) 3])}] }
        set fillet   [expr {$side_turn > $::CURVED_TURN}]
        set cylinder [expr {[lindex $LI($a) 3] > $::CURVED_TURN && [lindex $LI($b) 3] > $::CURVED_TURN}]
        if {!$fillet && !$cylinder} { continue }
        # V8 : the two long edges of a curved band (top / bottom of the fillet)
        # always get the same count, otherwise the band mesh tears between them.
        # A chain of pairs (a~b, b~c, ...) may only drift by BAND_DRIFT, else a
        # short line at the end of the chain would get the count of a long one.
        set ra [yl8_uf_find $a]; set rb [yl8_uf_find $b]
        if {![info exists GMIN($ra)]} { set GMIN($ra) $N($a); set GMAX($ra) $N($a) }
        if {![info exists GMIN($rb)]} { set GMIN($rb) $N($b); set GMAX($rb) $N($b) }
        set gmin [expr {min($GMIN($ra), $GMIN($rb))}]
        set gmax [expr {max($GMAX($ra), $GMAX($rb))}]
        if {$ra != $rb && $gmax > $::BAND_DRIFT * $gmin + 1} { incr ::yl8_unpaired; continue }
        lappend ::yl8_pairs [list $a $b $s]
        if {$ra != $rb} { set UF($ra) $rb }
        set GMIN($rb) $gmin; set GMAX($rb) $gmax
        # fillet band with two short curved sides (the cross-sections at its two
        # ends) : those must match as well
        if {$fillet && [llength $byl] == 4} {
            set c [lindex $byl 2 0]; set d [lindex $byl 3 0]
            if {[info exists FIX($c)] && [info exists FIX($d)]} {
                lappend ::yl8_pairs [list $c $d $s]
                set rc [yl8_uf_find $c]; set rd [yl8_uf_find $d]
                if {$rc != $rd} { set UF($rc) $rd }
            }
        }
    }
    # one count per group = the mean wish of the group (density stays at the
    # requested size; the largest wish made whole chains too dense)
    array set G {}
    array set GS {}
    array set GC {}
    array set GF {}
    foreach l $order {
        set r [yl8_uf_find $l]
        if {![info exists GS($r)]} { set GS($r) 0; set GC($r) 0 }
        incr GS($r) $N($l); incr GC($r)
        if {[info exists FIX($l)] && (![info exists GF($r)] || $N($l) > $GF($r))} { set GF($r) $N($l) }
    }
    foreach r [array names GS] {
        set G($r) [expr {max(1, int(round(double($GS($r)) / $GC($r))))}]
        # a group holding a fillet cross-section : the fillet rule decides
        # (largest fillet wish of the group, never above the fillet limit)
        if {[info exists GF($r)]} { set G($r) [expr {min($GF($r), $::FILLET_MAX_NODES + 1)}] }
    }
    # fillet cross-sections : their node count is locked in the clean-up as well
    set ::yl8_fillet_lines [array names FIX]
    foreach l $order { set N($l) $G([yl8_uf_find $l]) }
    array set seen {}
    foreach p $::yl8_pairs { set seen([yl8_uf_find [lindex $p 0]]) 1 }
    set n_groups [array size seen]

    # one interactive session for all surfaces
    set ok 1
    *clearmark surfs 1
    eval *createmark surfs 1 $sids
    if {[catch {*interactiveremeshsurf 1 $size 3 3 2 1 1}]} { set ok 0 }
    if {$ok} {
        set i 0
        foreach l $order {
            if {[catch {*set_meshedgeparams $i $N($l) 1 0 0 0 0 0 0}]} { set ok 0; break }
            incr i
        }
    }
    if {$ok} {
        for {set f 0} {$f < [llength $sids]} {incr f} { catch {*automesh $f 3 3} }
        if {[catch {*storemeshtodatabase 1}]} { set ok 0 }
    }
    catch { *ameshclearsurface }
    *clearmark surfs 1
    if {[info exists ::YL_DEBUG_FILE]} {
        # wanted vs. actual divisions per line (troubleshooting only)
        *clearmark elems 1; *createmark elems 1 all; *clearmark nodes 2
        catch { *findmark elems 1 1 1 nodes 0 2 }
        set pn {}
        foreach nd [hm_getmark nodes 2] { lappend pn $nd [lindex [hm_nodevalue $nd] 0] }
        *clearmark elems 1; *clearmark nodes 2
        set fh [open $::YL_DEBUG_FILE w]
        set i 0
        foreach l $order {
            set c [yl8_line_node_count $l $pn]
            puts $fh [format "idx %4d line %5d len %8.3f want %3d got %3d %s" $i $l [lindex $LI($l) 0] $N($l) [expr {$c-1}] [expr {$c-1 == $N($l) ? "" : "<<"}]]
            incr i
        }
        close $fh
    }
    set n_elem 0
    foreach s $sids { incr n_elem [yl8_surf_elem_count $s] }
    if {!$ok || $n_elem == 0} {
        # interactive session not available : old proximity buckets
        set ::yl8_pairs {}
        set ::yl8_fillet_lines {}
        yl8_delete_elems {tria3 quad4 tria6 quad8}
        return [lreplace [yl8_mesh_surfaces_buckets $surf_ids $size $min_f $growth] 0 0 0]
    }
    # surfaces left without elements : plain automesh, same as before
    set unmeshed {}
    foreach s $sids {
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        if {$a > 1e-9 && [yl8_surf_elem_count $s] == 0} { lappend unmeshed $s }
    }
    if {[llength $unmeshed] > 0} {
        *clearmark surfs 1
        eval *createmark surfs 1 $unmeshed
        catch {*defaultremeshsurf 1 $size 3 3 2 1 1 1 1 0 0 0 0}
        *clearmark surfs 1
        set still {}
        foreach s $unmeshed { if {[yl8_surf_elem_count $s] == 0} { lappend still $s } }
        set unmeshed $still
    }
    return [list $n_groups [llength $unmeshed]]
}

# number of mesh nodes lying on line l (geometric test, bounding box filtered)
proc yl8_line_node_count {l pts_by_node} { lindex [yl8_line_nodes $l $pts_by_node] 0 }
# returns {count node_ids}; count = interior nodes + end node(s), ids = the
# interior nodes of the line
proc yl8_line_nodes {l pts_by_node} {
    set ts {}
    for {set k 0} {$k <= 64} {incr k} { lappend ts [expr {$k / 64.0}] }
    set pts {}
    catch { set pts [hm_getcoordinatesofpointsonline $l $ts] }
    if {[llength $pts] < 2} { return [list -1 {}] }
    set len [yl8_line_length $l]
    # nodes are placed exactly on the CAD line; the polyline of 64 segments
    # follows it far closer than this tolerance
    set lim [expr {max(1e-4, min(0.01, 0.002 * $len))}]
    set x0 1e30; set y0 1e30; set z0 1e30; set x1 -1e30; set y1 -1e30; set z1 -1e30
    foreach q $pts {
        lassign $q a b c
        set x0 [expr {min($x0,$a)}]; set y0 [expr {min($y0,$b)}]; set z0 [expr {min($z0,$c)}]
        set x1 [expr {max($x1,$a)}]; set y1 [expr {max($y1,$b)}]; set z1 [expr {max($z1,$c)}]
    }
    # interior nodes only (+2 end points at the end): nodes of tiny neighbouring
    # lines sit right next to the end points and must not be counted
    set p_first [lindex $pts 0]; set p_last [lindex $pts end]
    set end_r [expr {max(2.0 * $lim, 0.02 * $len)}]
    set n 0
    set ids {}
    foreach {nd p} $pts_by_node {
        lassign $p x y z
        if {$x < $x0-$lim || $x > $x1+$lim || $y < $y0-$lim || $y > $y1+$lim || $z < $z0-$lim || $z > $z1+$lim} { continue }
        set d_first [qc::len [qc::sub $p $p_first]]
        set d_last  [qc::len [qc::sub $p $p_last]]
        # end nodes are corners shared with neighbouring (often tiny) lines:
        # neither counted here nor returned (they stay free for the clean-up)
        if {$d_first < $end_r || $d_last < $end_r} { continue }
        # distance from the node to the polyline (point - segment)
        set best 1e30
        lassign [lindex $pts 0] ax ay az
        foreach q [lrange $pts 1 end] {
            lassign $q bx by bz
            set dx [expr {$bx-$ax}]; set dy [expr {$by-$ay}]; set dz [expr {$bz-$az}]
            set dd [expr {$dx*$dx+$dy*$dy+$dz*$dz}]
            set t 0.0
            if {$dd > 1e-24} { set t [expr {max(0.0, min(1.0, (($x-$ax)*$dx+($y-$ay)*$dy+($z-$az)*$dz)/$dd))}] }
            set ex [expr {$ax+$t*$dx-$x}]; set ey [expr {$ay+$t*$dy-$y}]; set ez [expr {$az+$t*$dz-$z}]
            set d2 [expr {$ex*$ex+$ey*$ey+$ez*$ez}]
            if {$d2 < $best} { set best $d2 }
            set ax $bx; set ay $by; set az $bz
        }
        if {sqrt($best) < $lim} { incr n; lappend ids $nd }
    }
    # closed line (circle) : one end node
    if {[qc::len [qc::sub $p_first $p_last]] < $lim} { return [list [expr {$n + 1}] $ids] }
    return [list [expr {$n + 2}] $ids]
}
# nodes of the matched lines -> qc::LINE_OF, so that the clean-up cannot
# change their node count (collapse / split along them is refused)
proc yl8_lock_matched_lines {} {
    array unset qc::LINE_OF
    if {![info exists ::yl8_fillet_lines]} { set ::yl8_fillet_lines {} }
    if {[llength $::yl8_pairs] == 0 && [llength $::yl8_fillet_lines] == 0} { return 0 }
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    *clearmark nodes 2
    catch { *findmark elems 1 1 1 nodes 0 2 }
    set pn {}
    foreach nd [hm_getmark nodes 2] { lappend pn $nd [lindex [hm_nodevalue $nd] 0] }
    *clearmark elems 1
    *clearmark nodes 2
    array set done {}
    set n 0
    # matched band edges + fillet cross-sections (V8 : 1/2/3 nodes stay 1/2/3)
    set lines $::yl8_fillet_lines
    foreach p $::yl8_pairs { lappend lines {*}[lrange $p 0 1] }
    foreach p [list $lines] {
        foreach l $p {
            if {![hm_entityinfo exist lines $l]} { continue }
            if {[info exists done($l)]} { continue }
            set done($l) 1
            foreach nd [lindex [yl8_line_nodes $l $pn] 1] { lappend qc::LINE_OF($nd) $l; incr n }
        }
    }
    return $n
}
# interior nodes of curved CAD lines -> qc::KEEP_NODE (no collapse; splits allowed)
proc yl8_keep_curve_nodes {} {
    array unset qc::KEEP_NODE
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    *clearmark nodes 2
    catch { *findmark elems 1 1 1 nodes 0 2 }
    set pn {}
    foreach nd [hm_getmark nodes 2] { lappend pn $nd [lindex [hm_nodevalue $nd] 0] }
    *clearmark elems 1
    *clearmark nodes 2
    set n 0
    foreach l [yl8_live_lines] {
        if {[lindex [yl8_line_info $l] 3] <= $::CURVED_TURN} { continue }
        foreach nd [lindex [yl8_line_nodes $l $pn] 1] { set qc::KEEP_NODE($nd) 1; incr n }
    }
    return $n
}
# arcs that the aspect repair reduced to one straight element : put the nodes
# back on the CAD line (t = i/k) where this keeps every tria below the limit
proc yl8_restore_arcs {lim min_size} {
    if {[yl8_count_coarse_curves $min_size] == 0} { return 0 }
    set arcs {}
    foreach c $::yl8_coarse_lines {
        lassign $c l turn segs
        if {$segs != 1} { continue }
        set k [yl8_curve_divs [yl8_line_length $l] $turn [expr {$min_size / $::MIN_SIZE_FACTOR}] $min_size]
        if {$k < 2} { continue }
        set ts {0.0 1.0}
        for {set i 1} {$i < $k} {incr i} { lappend ts [expr {double($i) / $k}] }
        set pts {}
        catch { set pts [hm_getcoordinatesofpointsonline $l $ts] }
        if {[llength $pts] != [llength $ts]} { continue }
        lappend arcs [list [lindex $pts 0] [lindex $pts 1] [lrange $pts 2 end]]
    }
    if {[llength $arcs] == 0} { return 0 }
    return [qc::restore_arcs $arcs $lim]
}
# V8 : matched band edges with different node counts after the clean-up -> the
# line with fewer nodes gets its longest mesh edges split on the CAD line until
# both match (only where every tria stays below the limit). A chain of pairs
# can need several rounds. Returns the number of inserted nodes.
proc yl8_equalize_pairs {lim rounds} {
    set total 0
    for {set r 0} {$r < $rounds} {incr r} {
        if {[yl8_count_mismatch] == 0} { break }
        # one job per line : the largest deficit against any of its partners
        array unset need
        array set need {}
        foreach d $::yl8_mismatch_data {
            lassign $d a na b nb
            if {$na < $nb} { set l $a; set k [expr {$nb - $na}] } else { set l $b; set k [expr {$na - $nb}] }
            if {![info exists need($l)] || $k > $need($l)} { set need($l) $k }
        }
        set jobs {}
        foreach l [array names need] {
            set ts {}
            for {set k 0} {$k <= 64} {incr k} { lappend ts [expr {$k / 64.0}] }
            set pts {}
            catch { set pts [hm_getcoordinatesofpointsonline $l $ts] }
            if {[llength $pts] < 2} { continue }
            lappend jobs [list $pts $need($l)]
        }
        if {[llength $jobs] == 0} { break }
        set n [qc::equalize_lines $jobs $lim]
        incr total $n
        if {$n == 0} { break }
    }
    return $total
}
# nodes of the trias above the aspect limit, grown by <ring> element layers
proc yl8_bad_nodes {lim ring} {
    *clearmark elems 1
    *clearmark elems 2
    *createmark elems 1 "by config" tria3
    catch {*elementtestaspect elems 1 $lim 2 2 0 "2D Aspect Ratio"}
    set els [hm_getmark elems 2]
    *clearmark elems 1
    *clearmark elems 2
    set nds {}
    for {set r 0} {$r <= $ring && [llength $els] > 0} {incr r} {
        *clearmark elems 1
        eval *createmark elems 1 $els
        *clearmark nodes 2
        catch { *findmark elems 1 1 1 nodes 0 2 }
        set nds [hm_getmark nodes 2]
        *clearmark elems 1
        if {$r == $ring || [llength $nds] == 0} { break }
        # elements attached to those nodes = next layer
        *clearmark nodes 1
        eval *createmark nodes 1 $nds
        *clearmark elems 2
        catch { *findmark nodes 1 1 1 elems 0 2 }
        set els [hm_getmark elems 2]
        *clearmark nodes 1
        *clearmark elems 2
    }
    *clearmark nodes 2
    return $nds
}
# matched pairs whose node counts differ in the current mesh
proc yl8_count_mismatch {} {
    if {[llength $::yl8_pairs] == 0} { return 0 }
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    *clearmark nodes 2
    catch { *findmark elems 1 1 1 nodes 0 2 }
    set pn {}
    foreach nd [hm_getmark nodes 2] { lappend pn $nd [lindex [hm_nodevalue $nd] 0] }
    *clearmark elems 1
    *clearmark nodes 2
    set bad 0
    set ::yl8_mismatch_list {}
    set ::yl8_mismatch_data {}
    foreach p $::yl8_pairs {
        lassign $p a b s
        if {![hm_entityinfo exist lines $a] || ![hm_entityinfo exist lines $b]} { continue }
        set na [yl8_line_node_count $a $pn]
        set nb [yl8_line_node_count $b $pn]
        if {$na != $nb} {
            incr bad
            lappend ::yl8_mismatch_data [list $a $na $b $nb]
            lappend ::yl8_mismatch_list [format "surf %d: line %d=%d (len %.1f) line %d=%d (len %.1f)" $s $a $na [yl8_line_length $a] $b $nb [yl8_line_length $b]]
        }
    }
    return $bad
}

# curved CAD lines meshed too coarsely : turn per element above 2 x MAX_ARC_ANGLE
# (and the line long enough for one more node at the minimum size)
proc yl8_count_coarse_curves {min_size} {
    set ::yl8_coarse_list {}
    set ::yl8_coarse_lines {}
    set ::yl8_manual_list {}
    # largest interior node count on a fillet cross-section (V8 rule : <= FILLET_MAX_NODES)
    set ::yl8_fillet_max 0
    set ::yl8_fillet_over {}
    *clearmark elems 1
    *createmark elems 1 "by config" tria3
    *clearmark nodes 2
    catch { *findmark elems 1 1 1 nodes 0 2 }
    set pn {}
    foreach nd [hm_getmark nodes 2] { lappend pn $nd [lindex [hm_nodevalue $nd] 0] }
    *clearmark elems 1
    *clearmark nodes 2
    if {[llength $pn] == 0} { return 0 }
    set bad 0
    foreach l [yl8_live_lines] {
        lassign [yl8_line_info $l] len p0 p1 turn
        if {$turn <= $::CURVED_TURN} { continue }
        set c [yl8_line_node_count $l $pn]
        if {$c < 2} { continue }
        set segs [expr {$c - 1}]
        set size [expr {$min_size / $::MIN_SIZE_FACTOR}]
        set target [yl8_curve_divs $len $turn $size $min_size]
        if {[yl8_is_fillet_arc $len $turn $size]} {
            set need [expr {min($target, max(2, $target - 1))}]
        } else {
            set need [expr {min($target, int(ceil($turn / (2.0 * $::ARC_ANGLE_LONG))))}]
        }
        if {[yl8_is_fillet_arc $len $turn $size]} {
            if {$segs - 1 > $::yl8_fillet_max} { set ::yl8_fillet_max [expr {$segs - 1}] }
            if {$segs - 1 > $::FILLET_MAX_NODES} {
                lappend ::yl8_fillet_over [format "line %d: len %.2f turn %.0f deg, %d interior node(s), rule %d" $l $len $turn [expr {$segs - 1}] [expr {$target - 1}]]
            }
        }
        if {$segs < $need && $len / ($segs + 1) >= $min_size} {
            # arc radius below the minimum element size : cannot be meshed
            # finer at aspect <= limit -> not a failure, reported for manual work
            set radius [expr {$len / ($turn * acos(-1.0) / 180.0)}]
            if {$radius < $min_size} {
                set pm {}
                catch { set pm [lindex [hm_getcoordinatesofpointsonline $l {0.5}] 0] }
                lassign $pm mx my mz
                if {$mx eq ""} { set mx 0; set my 0; set mz 0 }
                lappend ::yl8_manual_list [format "line %d (R %.2f mm, turn %.0f deg, %d element(s)) at x=%.2f y=%.2f z=%.2f" $l $radius $turn $segs $mx $my $mz]
                continue
            }
            incr bad
            lappend ::yl8_coarse_list [format "line %d: turn %.0f deg, %d element(s)" $l $turn $segs]
            lappend ::yl8_coarse_lines [list $l $turn $segs]
        }
    }
    return $bad
}

# ---------------- fallback : proximity size buckets + R-Trias mesh ----------------
# returns list {n_buckets n_unmeshed}
proc yl8_mesh_surfaces_buckets {surf_ids size min_f growth} {
    set min_size [expr {$min_f * $size}]
    array set bucket {}
    foreach s $surf_ids {
        set w [yl8_surf_width $s]
        set local $size
        if {$w > 1e-9} {
            # same rule as yl8_mesh_surfaces : only very narrow strips are reduced
            set local [expr {max($min_size, min($size, $::STRIP_ELONGATION * $w))}]
        }
        # snap to the bucket ladder size / growth^k
        set k 0
        set bs $size
        while {$bs > $local * 1.0001 && $k < 12} { set bs [expr {$bs / $growth}]; incr k }
        lappend bucket($k) $s
    }
    set ks [lsort -integer -decreasing [array names bucket]]
    foreach k $ks {
        set bs [expr {$size / pow($growth, $k)}]
        *clearmark surfs 1
        eval *createmark surfs 1 $bucket($k)
        # mark size elem_type(3 = R-trias) elem_type2 ... (same flags YL_V6 records)
        if {[catch {*defaultremeshsurf 1 $bs 3 3 2 1 1 1 1 0 0 0 0}]} {
            catch {*defaultremeshsurf 1 $bs 0 0 2 1 1 1 1 0 0 0 0}
        }
        *clearmark surfs 1
    }
    # surfaces that did not mesh : one more try with plain trias
    set unmeshed {}
    foreach s $surf_ids {
        if {![hm_entityinfo exist surfs $s]} { continue }
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        if {$a > 1e-9 && [yl8_surf_elem_count $s] == 0} { lappend unmeshed $s }
    }
    if {[llength $unmeshed] > 0} {
        *clearmark surfs 1
        eval *createmark surfs 1 $unmeshed
        catch {*defaultremeshsurf 1 $size 0 0 2 1 1 1 1 0 0 0 0}
        *clearmark surfs 1
        set still {}
        foreach s $unmeshed { if {[yl8_surf_elem_count $s] == 0} { lappend still $s } }
        set unmeshed $still
    }
    return [list [llength $ks] [llength $unmeshed]]
}

# ---------------- 4. quality gated smoothing ----------------
# Laplacian move of interior nodes (rank 0 in qc::classify : not on a feature
# line, not a corner), snapped back onto the CAD surface. A move is kept only
# when the worst aspect of the touched trias gets better (or stays and the mean
# improves) and no tria folds over. Uses the in-memory mesh of YL_qclean.tcl.
proc yl8_smooth {size feature_deg passes} {
    set qc::COSF [expr {cos($feature_deg * acos(-1.0) / 180.0)}]
    set n [qc::load]
    if {$n == 0} { return 0 }
    qc::classify
    array set moved {}
    for {set p 0} {$p < $passes} {incr p} {
        set changed 0
        foreach nd [array names qc::NE] {
            if {[qc::rank $nd] != 0} { continue }
            set els $qc::NE($nd)
            if {[llength $els] < 3} { continue }
            set nb [qc::nbrs $nd]
            if {[llength $nb] < 3} { continue }
            set cx 0.0; set cy 0.0; set cz 0.0
            foreach m $nb { lassign $qc::X($m) x y z; set cx [expr {$cx+$x}]; set cy [expr {$cy+$y}]; set cz [expr {$cz+$z}] }
            set k [expr {double([llength $nb])}]
            set c [list [expr {$cx/$k}] [expr {$cy/$k}] [expr {$cz/$k}]]
            set old $qc::X($nd)
            if {[qc::len [qc::sub $c $old]] < 1e-4 * $size} { continue }
            set pnew [qc::snap_point $c $els [expr {0.5 * $size}]]
            set w0 0.0; set s0 0.0; set nrm {}
            foreach e $els {
                lassign [qc::etri $qc::E($e)] asp nn ar
                if {$asp > $w0} { set w0 $asp }
                set s0 [expr {$s0 + $asp}]
                lappend nrm $nn
            }
            set qc::X($nd) $pnew
            set w1 0.0; set s1 0.0; set ok 1; set i 0
            foreach e $els {
                lassign [qc::etri $qc::E($e)] asp nn ar
                if {$ar < 1e-9 || [qc::dot $nn [lindex $nrm $i]] < 0.8} { set ok 0; break }
                if {$asp > $w1} { set w1 $asp }
                set s1 [expr {$s1 + $asp}]
                incr i
            }
            if {$ok && ($w1 < $w0 - 1e-6 || ($w1 <= $w0 + 1e-6 && $s1 < $s0 - 1e-6))} {
                set moved($nd) 1
                incr changed
            } else {
                set qc::X($nd) $old
            }
        }
        if {$changed == 0} { break }
    }
    set nm 0
    foreach nd [array names moved] {
        lassign $qc::X($nd) x y z
        if {![catch {*nodemodify $nd $x $y $z}]} { incr nm }
    }
    return $nm
}

# ---------------- surfaces ----------------
*clearmark surfs 1
if {$::YL_BATCH} {
    *createmark surfs 1 "all"
} else {
    *createmarkpanel surfs 1 "Select surfaces for YL_V8 R-Trias Mesh (Size = ${ELEM_SIZE}mm)"
}
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1
if {[llength $surf_ids] == 0 && !$::YL_BATCH} {
    *createmark surfs 1 "displayed"
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
}
if {[llength $surf_ids] == 0} {
    set ::YL_RESULT [list status "FAIL" msg "No surfaces found."]
    if {!$::YL_BATCH} { tk_messageBox -message "No surfaces found." }
    return
}

# old mesh on these surfaces / old tetras
array unset yl8_comps
foreach s $surf_ids {
    if {![catch {hm_getvalue surfs id=$s dataname=collector.id} cid] && $cid ne ""} { set yl8_comps($cid) 1 }
}
if {[array size yl8_comps] > 0} {
    *clearmark elems 1
    eval *createmark elems 1 {"by comp id"} [array names yl8_comps]
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
}
yl8_delete_elems {tetra4 tetra10}
*clearmark elems 1
catch { *ameshclearsurface }
catch { *elementorder 1 }
catch { *setedgedensitylink 0 }

# Stages 1-6 run up to twice: suppressing edges can leave a T-connection or a
# free edge in the skin (surfaces merged across a non-manifold spot) or a band
# that cannot be repaired. When the first attempt fails the 2D check, the
# geometry snapshot taken before the defeature is loaded back and everything is
# meshed again without it.
set n_surf_raw [llength $surf_ids]
set geom_snapshot "$OUT_DIR/_yl8_geom_[pid].hm"
catch { hm_answernext yes }
set has_snapshot [expr {![catch { *writefile "$geom_snapshot" 1 }] && [file exists $geom_snapshot]}]
set defeature_retry 0
set tiny_f  $DEFEATURE_TINY
set short_f $DEFEATURE_SHORT

for {set attempt 1} {$attempt <= 2} {incr attempt} {

# ---------------- 1. defeature ----------------
set n_defeatured 0
if {$tiny_f > 0 || $short_f > 0} {
    catch { set n_defeatured [yl8_defeature $ELEM_SIZE $SHARP_ANGLE $tiny_f $short_f] }
}
# suppressing edges merges surfaces (new ids) : take the surface list again.
# NOTE: in GUI mode a partial selection is widened to all surfaces here.
*clearmark surfs 1
*createmark surfs 1 "all"
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1

# ---------------- 2+3. size field + R-Trias mesh ----------------
set n_buckets 0
set n_unmeshed 0
set mesh_err ""
set ::yl8_pairs {}
if {![catch {yl8_mesh_surfaces $surf_ids $ELEM_SIZE $MIN_SIZE_FACTOR $SIZE_GROWTH} mesh_res]} {
    lassign $mesh_res n_buckets n_unmeshed
} else {
    set mesh_err $mesh_res
}
set qc::KEEP_SURF_EDGES $KEEP_SURF_EDGES
set qc::LOCK_FEAT_COUNT $LOCK_FEAT_COUNT
*clearmark elems 1
*createmark elems 1 "by config" quad4 quad8
if {[llength [hm_getmark elems 1]] > 0} { catch { *splitelements 2 1 } }
*clearmark elems 1
*createmark elems 1 "by config" tria3
if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 0.01 1 0 0 } }
*clearmark elems 1

set n_raw    [yl8_count tria3]
set bad_raw  [yl8_bad_aspect $TARGET_ASPECT]
set free_raw [yl8_edges 0]
set t_raw    [yl8_edges 1]
set mis_raw  [yl8_count_mismatch]
set curv_raw -1
catch { set curv_raw [yl8_count_coarse_curves [expr {$MIN_SIZE_FACTOR * $ELEM_SIZE}]] }
set curv_raw_list $::yl8_coarse_list
set fillet_max_raw $::yl8_fillet_max
set fillet_over_raw $::yl8_fillet_over
if {[info exists ::env(YL_DEBUG_BAD)] && $::env(YL_DEBUG_BAD) ne ""} {
    # troubleshooting : which surfaces hold the raw trias above the aspect limit
    catch {
        *clearmark elems 1; *clearmark elems 2
        *createmark elems 1 "by config" tria3
        *elementtestaspect elems 1 $TARGET_ASPECT 2 2 0 "2D Aspect Ratio"
        array unset dbg_bad; array set dbg_bad {}
        foreach e [hm_getmark elems 2] { set dbg_bad($e) 1 }
        *clearmark elems 1; *clearmark elems 2
        set fh [open $::env(YL_DEBUG_BAD) a]
        puts $fh "=== $BASE_NAME attempt $attempt : [array size dbg_bad] bad raw trias"
        *createmark surfs 1 all
        set dbg_surfs [hm_getmark surfs 1]
        foreach s $dbg_surfs {
            *clearmark surfs 1; *clearmark elems 2
            *createmark surfs 1 $s
            if {[catch {*findmark surfs 1 257 1 elems 0 2}]} { continue }
            set els [hm_getmark elems 2]
            set nb 0
            foreach e $els { if {[info exists dbg_bad($e)]} { incr nb } }
            if {$nb == 0} { continue }
            set ed {}
            foreach l [yl8_surf_edges $s] {
                lassign [yl8_line_info $l] len p0 p1 turn
                lappend ed [format "%d:L%.1f/t%.0f" $l $len $turn]
            }
            set a 0.0; catch { set a [hm_getareaofsurface surfs $s] }
            puts $fh [format "surf %4d bad %3d of %4d  width %.2f area %.1f  edges %s" $s $nb [llength $els] [yl8_surf_width $s] $a $ed]
        }
        *clearmark surfs 1; *clearmark elems 2
        close $fh
    }
}
set n_locked 0
catch { set n_locked [yl8_lock_matched_lines] }
set n_curve_kept 0
catch { set n_curve_kept [yl8_keep_curve_nodes] }

# ---------------- 4. smoothing ----------------
set n_smoothed 0
set smooth_err ""
if {$n_raw > 0 && [catch {yl8_smooth $ELEM_SIZE $SHARP_ANGLE $SMOOTH_PASSES} n_smoothed]} {
    set smooth_err $n_smoothed
    set n_smoothed 0
}
set bad_smooth [yl8_bad_aspect $TARGET_ASPECT]

# ---------------- 5. improve (YL_qclean) ----------------
set qc_err ""
set qc_res {}
if {$n_raw > 0 && [catch {qc::run $TARGET_ASPECT $ELEM_SIZE $SHARP_ANGLE} qc_res]} {
    set qc_err $qc_res
    set qc_res {}
}
array unset QS
array set QS {collapse 0 flip 0 split 0 quadsplit 0 needlesplit 0 passes 0}
array set QS $qc_res
# aspect has priority : elements the locked pass could not repair get a second
# pass with the matched lines released (those few bands may then differ by a node)
set qc_unlocked 0
# the locks (matched line counts, curved line nodes) are released locally only:
# first the nodes of the bad trias, then one / two element layers around them.
# Everything else keeps its seeded node count, so the faces stay evenly meshed
# (curv_coarse / edge_mismatch report what was lost).
# V8 order : the curved line nodes are released first, the matched band edges
# (top / bottom count of a fillet) only when that is not enough:
#   stage 0 : curve nodes at the bad trias        stage 1 : curve nodes, 1 layer around
#   stage 2 : + band edge nodes at the bad trias  stage 3 : + band edge nodes, 1 layer around
#   stage 4 : everything (2D pass has priority)
set qc_curve_released 0
for {set stage 0} {$stage <= 4} {incr stage} {
    if {$qc_err ne "" || ([array size qc::KEEP_NODE] == 0 && [array size qc::LINE_OF] == 0) || [yl8_bad_aspect $TARGET_ASPECT] == 0} { break }
    set qc_curve_released [expr {$stage + 1}]
    set qc_unlocked 1
    if {$stage == 4} {
        array unset qc::KEEP_NODE
        array unset qc::LINE_OF
    } else {
        set ring [expr {$stage % 2}]
        set free_lines [expr {$stage >= 2}]
        foreach nd [yl8_bad_nodes $TARGET_ASPECT $ring] {
            catch { unset qc::KEEP_NODE($nd) }
            if {$free_lines} { catch { unset qc::LINE_OF($nd) } }
        }
    }
    if {[catch {qc::run $TARGET_ASPECT $ELEM_SIZE $SHARP_ANGLE} qc_res3]} {
        set qc_err $qc_res3
    } else {
        foreach {k v} $qc_res3 { if {$k in {collapse flip split quadsplit needlesplit}} { set QS($k) [expr {$QS($k) + $v}] } }
    }
}
array unset qc::KEEP_NODE
array unset qc::LINE_OF
set n_arc_restored 0
set qc::restore_why {}
if {$qc_curve_released} {
    if {[catch {yl8_restore_arcs $TARGET_ASPECT [expr {$MIN_SIZE_FACTOR * $ELEM_SIZE}]} n_arc_restored]} {
        set qc_err "arc restore: $n_arc_restored"
        set n_arc_restored 0
    }
}
# V8 : top / bottom of every matched band back to the same node count
set n_equalized 0
if {$qc_curve_released && $qc_err eq ""} {
    if {[catch {yl8_equalize_pairs $TARGET_ASPECT 3} n_equalized]} {
        set qc_err "equalize: $n_equalized"
        set n_equalized 0
    }
}

# ---------------- 6. 2D check ----------------
set n_2d_skin     [yl8_count tria3]
set bad_2d_cnt    [yl8_bad_aspect $TARGET_ASPECT]
set free_edge_cnt [yl8_edges 0]
set t_edge_cnt    [yl8_edges 1]
set mis_final     [yl8_count_mismatch]
set curv_coarse   -1
set ::yl8_fillet_max -1
catch { set curv_coarse [yl8_count_coarse_curves [expr {$MIN_SIZE_FACTOR * $ELEM_SIZE}]] }
set fillet_max_nodes $::yl8_fillet_max
set band_unpaired 0
if {[info exists ::yl8_unpaired]} { set band_unpaired $::yl8_unpaired }
set avg_size 0.0
catch { set avg_size [yl8_avg_elem_size $n_2d_skin] }
set curv_manual_list {}
if {[info exists ::yl8_manual_list]} { set curv_manual_list $::yl8_manual_list }
set pass2d [expr {$n_2d_skin > 0 && $bad_2d_cnt == 0 && $free_edge_cnt == 0 && $t_edge_cnt == 0 ? 1 : 0}]

# 2D failed after the defeature -> load the geometry back and retry without it
if {!$pass2d && $attempt == 1 && $n_defeatured > 0 && $has_snapshot} {
    catch { hm_answernext yes }
    if {![catch { *readfile "$geom_snapshot" }]} {
        set defeature_retry 1
        set tiny_f 0
        set short_f 0
        continue
    }
}
break
}
catch { file delete $geom_snapshot }

# ---------------- 7. save 2D / run 3D ----------------
set n_tet 0
set tet_err ""
set tet_check "not run"
set bad_tet_collapse_cnt 0
set mode_label [expr {$DO_TETRA ? "3D_${ABAQUS_ELEM_TYPE}" : "2D_Only"}]
set out_stem "${BASE_NAME}_${mode_label}_${ELEM_SIZE}mm"
if {[info exists ::YL_OUT_NAME] && [string trim $::YL_OUT_NAME] ne ""} { set out_stem [string trim $::YL_OUT_NAME] }
set out_hm "$OUT_DIR/${out_stem}.hm"
set out_inp ""
set inp_err ""
set EXPORT_INP 0
if {[info exists ::YL_EXPORT_INP]} { set EXPORT_INP $::YL_EXPORT_INP }
set write_failed 0
set write_err ""
set tet_skipped 0

if {!$DO_TETRA} {
    catch { hm_answernext yes }
    set write_failed [catch { *writefile "$out_hm" 1 } write_err]
} elseif {!$pass2d} {
    # 2D did not pass : no tetra at all, keep the 2D result for inspection
    set tet_skipped 1
    set tet_check "skipped (2D not passed)"
    catch { hm_answernext yes }
    set write_failed [catch { *writefile "$out_hm" 1 } write_err]
} else {
    catch { *templatefileset "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d" }
    set tet_err [yl8_tetmesh $TET_GROWTH]
    set n_tet [yl8_count {tetra4 tetra10}]
    set tet_check [expr {$n_tet > 0 ? "OK ($n_tet tetras)" : "FAILED"}]

    if {$n_tet > 0} {
        if {$DELETE_2D_SKIN} { yl8_delete_elems {tria3 tria6} }
        # Abaqus type numbers from templates/feoutput/abaqus/standard.3d
        #   config 204 (tetra4) : 1 C3D4, 2 C3D4H     config 210 (tetra10) : 1 C3D10, 2 C3D10H, 3 C3D10M
        array set yl8_types {
            C3D4 {204 1} C3D4H {204 2} DC3D4 {204 3} C3D4E {204 4} DC3D4E {204 5}
            AC3D4 {204 6} FC3D4 {204 7} C3D4T {204 8} C3D4P {204 9}
            C3D10 {210 1} C3D10H {210 2} C3D10M {210 3} C3D10MH {210 4} DC3D10 {210 5}
            C3D10E {210 6} DC3D10E {210 7} AC3D10 {210 8} C3D10MP {210 9} C3D10MPH {210 10}
            C3D10MT {210 11} C3D10I {210 12} C3D10HS {210 13}
        }
        set yl8_type_unknown ""
        if {![info exists yl8_types($ABAQUS_ELEM_TYPE)]} {
            set yl8_type_unknown "unknown element type '$ABAQUS_ELEM_TYPE'"
            set ABAQUS_ELEM_TYPE "C3D4"
        }
        lassign $yl8_types($ABAQUS_ELEM_TYPE) yl8_cfg yl8_typ
        if {$yl8_cfg == 210} {
            *clearmark elems 1
            *createmark elems 1 "by config" tetra4
            # 2nd argument 1 : put the mid nodes on the CAD geometry
            if {[llength [hm_getmark elems 1]] > 0} { catch { *orderchangetosecond 1 1 } }
        }
        *clearmark elems 1
        *createmark elems 1 "by config" [expr {$yl8_cfg == 210 ? "tetra10" : "tetra4"}]
        set yl8_e0 [lindex [hm_getmark elems 1] 0]
        catch { *elementtype $yl8_cfg $yl8_typ }
        catch { *elementsettypes 1 }
        set tet_type ""
        catch { set tet_type [hm_getvalue elems id=$yl8_e0 dataname=typename] }
        *clearmark elems 1
        *clearmark elems 2
        *createmark elems 1 "by config" tetra4 tetra10
        if {![catch {*elementtesttetcollapse elems 1 $MIN_TET_COLLAPSE 2 2 "3D Tet Collapse"}]} {
            set bad_tet_collapse_cnt [llength [hm_getmark elems 2]]
        }
        *clearmark elems 1
        *clearmark elems 2
        set n_tet [yl8_count {tetra4 tetra10}]
        set tet_check "OK ([yl8_count tetra4] tetra4 + [yl8_count tetra10] tetra10, type $tet_type, tet collapse < $MIN_TET_COLLAPSE : $bad_tet_collapse_cnt)"
        if {$tet_type ne $ABAQUS_ELEM_TYPE} { set tet_err "element type is '$tet_type', wanted $ABAQUS_ELEM_TYPE" }
        if {$yl8_type_unknown ne ""} { set tet_err $yl8_type_unknown }
    }
    catch { hm_answernext yes }
    set write_failed [catch { *writefile "$out_hm" 1 } write_err]
    if {$EXPORT_INP && $n_tet > 0} {
        # same call HyperMesh records for File > Export > Solver Deck (Abaqus)
        set out_inp "$OUT_DIR/${out_stem}.inp"
        catch { file delete $out_inp }
        catch { hm_answernext yes }
        if {[catch {
            *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"
            *feoutputwithdata "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d" $out_inp 0 0 2 1 2
        } inp_err]} { set inp_err "INP EXPORT FAILED: $inp_err" } else { set inp_err "" }
        if {$inp_err eq "" && ![file exists $out_inp]} { set inp_err "INP EXPORT FAILED: file not written" }
    }
}

# ---------------- 8. report ----------------
set msg [format "YL_V8 (Ansys-style 2D -> 3D only after pass) finished\n\n- Target Mesh Size: %.2f mm\n- Defeature: %d small features merged away (surfaces %d -> %d), matched edge groups: %d, unmeshed surfaces: %d\n- 2D R-Trias Elements: %d (raw mesh: %d)\n- Aspect > %.1f : %d (raw: %d, after smoothing: %d)\n- Smoothing: %d nodes moved;  Clean-up: %d collapses, %d flips, %d strip/edge splits\n- Free Edges: %d, T-Edges: %d (raw mesh: %d, %d)\n- 2D PASS: %s\n- Tetra mesh: %s\n\nSaved: %s" \
    $ELEM_SIZE $n_defeatured $n_surf_raw [llength $surf_ids] $n_buckets $n_unmeshed \
    $n_2d_skin $n_raw $TARGET_ASPECT $bad_2d_cnt $bad_raw $bad_smooth \
    $n_smoothed $QS(collapse) $QS(flip) [expr {$QS(split) + $QS(quadsplit) + $QS(needlesplit)}] \
    $free_edge_cnt $t_edge_cnt $free_raw $t_raw [expr {$pass2d ? "YES" : "NO"}] $tet_check $out_hm]

set _status "OK"
set _why {}
if {$write_failed || ![file exists $out_hm]} { lappend _why "SAVE FAILED: $write_err" }
if {$inp_err ne ""}                          { lappend _why $inp_err }
if {$out_inp ne ""}                          { append msg "\nAbaqus deck: $out_inp" }
append msg "\n- Opposite edges (fillet/cylinder top-bottom) with different node counts: $mis_final of [llength $::yl8_pairs] (raw mesh: $mis_raw)"
if {$mis_final > 0} { foreach m [lrange $::yl8_mismatch_list 0 9] { append msg "\n    $m" } }
append msg [format "\n- Average 2D element size: %.2f mm (target %.2f mm)" $avg_size $ELEM_SIZE]
append msg "\n- Fillet cross-sections : at most $fillet_max_nodes interior node(s) (rule: 1 narrow / 2 medium / 3 wide, limit $FILLET_MAX_NODES)"
append msg " (raw mesh: $fillet_max_raw)"
foreach m [lrange $fillet_over_raw 0 9]   { append msg "\n    raw over the limit: $m" }
foreach m [lrange $::yl8_fillet_over 0 9] { append msg "\n    over the limit: $m" }
append msg "\n- Curved bands left unmatched (count drift > $BAND_DRIFT x along a chain): $band_unpaired"
append msg "\n- Curved edges that lost their nodes (fillet: below rule - 1, other arcs: > [expr {2.0 * $ARC_ANGLE_LONG}] deg per element): $curv_coarse (raw mesh: $curv_raw)"
foreach m [lrange $curv_raw_list 0 9] { append msg "\n    raw $m" }
if {$curv_coarse > 0} { foreach m [lrange $::yl8_coarse_list 0 9] { append msg "\n    $m" } }
foreach w [lrange $qc::restore_why 0 9] { append msg "\n    arc restore: $w" }
if {[llength $curv_manual_list] > 0} {
    append msg "\n!!! MANUAL MESH NEEDED: [llength $curv_manual_list] fillet(s) smaller than the minimum element size ([format %.2f [expr {$MIN_SIZE_FACTOR * $ELEM_SIZE}]] mm) stay faceted (not a 2D failure)"
    foreach m $curv_manual_list { append msg "\n!!!   $m" }
}
if {$n_equalized > 0}   { append msg "\nNOTE: $n_equalized node(s) inserted so that both edges of a matched band have the same count again" }
if {$n_arc_restored > 0} { append msg "\nNOTE: $n_arc_restored node(s) put back on arcs reduced to one element by the clean-up" }
if {$qc_curve_released} { append msg "\nNOTE: aspect repair released the node-count locks [expr {$qc_curve_released == 5 ? "everywhere" : "around the bad trias only (stage [expr {$qc_curve_released - 1}]: [expr {$qc_curve_released >= 3 ? "curve + band edge nodes" : "curve nodes only"}], ring [expr {($qc_curve_released - 1) % 2}])"}]" }
if {$mesh_err ne ""}                         { append msg "\nNOTE: edge-sized meshing error, bucket fallback used: $mesh_err" }
if {$defeature_retry}                        { append msg "\nNOTE: the 2D check failed after the defeature - meshed again from the original geometry without defeature" }
if {$n_2d_skin == 0}                         { lappend _why "NO 2D ELEMENTS CREATED" }
if {$n_unmeshed > 0}                         { lappend _why "$n_unmeshed SURFACE(S) COULD NOT BE MESHED" }
if {$smooth_err ne ""}                       { lappend _why "SMOOTHING ERROR: $smooth_err" }
if {$qc_err ne ""}                           { lappend _why "CLEAN-UP ERROR: $qc_err" }
if {$bad_2d_cnt != 0}                        { lappend _why "$bad_2d_cnt ELEMENT(S) STILL ABOVE ASPECT $TARGET_ASPECT" }
if {$free_edge_cnt != 0 || $t_edge_cnt != 0} { lappend _why "SKIN NOT CLOSED (free=$free_edge_cnt, T=$t_edge_cnt)" }
if {$tet_skipped}                            { lappend _why "2D NOT PASSED, 3D SKIPPED" }
if {$tet_check eq "FAILED"}                  { lappend _why "TETRA MESH NOT CREATED $tet_err" }
if {$tet_check ne "FAILED" && $tet_err ne ""} { lappend _why "3D: $tet_err" }
if {[llength $_why] > 0} {
    set _status "FAIL"
    foreach w $_why { append msg "\n*** $w" }
}
set ::YL_RESULT [list status $_status msg $msg out $out_hm size $ELEM_SIZE \
    etype [expr {$DO_TETRA ? $ABAQUS_ELEM_TYPE : "2D"}] inp $out_inp \
    surfs [llength $surf_ids] n2d_raw $n_raw bad2d_raw $bad_raw n2d $n_2d_skin bad2d $bad_2d_cnt \
    free_edges $free_edge_cnt t_edges $t_edge_cnt ntet $n_tet badtet $bad_tet_collapse_cnt \
    collapse $QS(collapse) flip $QS(flip) split [expr {$QS(split) + $QS(quadsplit) + $QS(needlesplit)}] \
    pass2d $pass2d defeatured $n_defeatured defeature_retry $defeature_retry \
    edge_pairs [llength $::yl8_pairs] edge_mismatch $mis_final curv_coarse $curv_coarse avg_size [format %.3f $avg_size] curv_manual [llength $curv_manual_list] \
    curv_manual_where [join $curv_manual_list " ; "] \
    engine "V8" fillet_max_nodes $fillet_max_nodes band_unpaired $band_unpaired]

if {!$::YL_BATCH} { tk_messageBox -message $msg }
'@

$rc = @(Start-YL) | Select-Object -Last 1
exit ([int]$rc)
