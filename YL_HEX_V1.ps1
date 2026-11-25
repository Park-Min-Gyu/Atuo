# =====================================================================
# YL_HEX_V1.ps1 : YL 자동 핵사(Hexa) 메쉬 (3D Multi-Solid Map + 2D Quad 스킨)
#   실행 : 같은 폴더의 YL_HEX_V1.bat 더블클릭 -> 설정 창 -> 모델 선택 -> [실행]
#   필요한 것 : HyperMesh 2019 (hmbatch.exe) + 라이선스
#
#   구성 : 1) 설정 창 + hmbatch 실행 (PowerShell)
#          2) 파일 끝 : HyperMesh 엔진 Tcl 파일들 (%TEMP%\YL_HEX_V1_engine 에 전개)
#             - run_batch.tcl     : 배치 드라이버 + CSV/로그 출력
#             - YL_H1_batch.tcl   : 1차 엔진 (3D Multi-Solid Map Hexa + Abaqus 타입/INP)
#             - YL_H2_batch.tcl   : 2차 엔진 (Fallback: BatchMesher 2D Quad/Mixed 스킨)
#             - YL_HEX_qclean.tcl : 3D Hex8 위상 보존형 Scaled Jacobian 내부 절점 스무딩
#             - YL_HEX_stats.tcl  : 2D Quad / 3D Hex8 독립 품질 통계
#
#   설정은 %LOCALAPPDATA%\YL_HEX_V1\YL_settings.json 에 저장됩니다.
#   hmbatch 경로 지정 : set YL_HMBATCH=<hmbatch.exe 경로>
#   SolidMap 플래그 지정(필요 시) : set YL_SOLIDMAP_FLAGS=131074
#   창 없이 실행 : set YL_JOB_FILE=<job 파일> (선택: set YL_OUT=<출력 폴더>)
# =====================================================================
$ErrorActionPreference = 'Continue'
$here  = $PSScriptRoot
$inv   = [Globalization.CultureInfo]::InvariantCulture
$utf8  = New-Object Text.UTF8Encoding $false

# ---------------- engine files ($YLEngineFiles, end of this file) -> temp folder ----------------
function Expand-YLEngine {
    $dir = Join-Path $env:TEMP 'YL_HEX_V1_engine'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    foreach ($name in $script:YLEngineFiles.Keys) {
        [IO.File]::WriteAllText((Join-Path $dir $name), $script:YLEngineFiles[$name], $utf8)
    }
    return $dir
}

# ---------------- hmbatch run ----------------
function Invoke-YLBatch {
    param(
        [string]$JobFile,
        [double]$ElemSize = 2.0,
        [int]$DoHex = 1,
        [double]$Aspect = 5.0,
        [double]$MinJacobian = 0.50,
        [double]$MaxWarpage = 20.0,
        [double]$MaxSkew = 60.0,
        [string]$QuadMode = 'mixed',
        [string]$ElemType = 'C3D8H',
        [int]$ExportInp = 0,
        [int]$QuadFallback = 1,
        [string]$OutDir,
        [string]$HmBatch = ''
    )
    $jobLines = @(Get-Content -LiteralPath $JobFile -Encoding UTF8 | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') })
    $all = @($jobLines | ForEach-Object { ($_ -split "`t")[0] })
    if (-not $all) { Write-Error 'job 파일에 모델이 없습니다.'; return 2 }

    if (-not $HmBatch) {
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
    $LogDir = Join-Path $OutDir '_logs'
    $InpDir = Join-Path $OutDir 'inp'
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $runLines = @($jobLines)
    $env:YL_EXPORT_INP    = "$ExportInp"
    $env:YL_OUT_DIR       = $OutDir
    $env:YL_LOG_DIR       = $LogDir
    $env:YL_ELEM_SIZE     = $ElemSize.ToString($inv)
    $env:YL_DO_HEX        = "$DoHex"
    $env:YL_ELEM_TYPE     = $ElemType
    $env:YL_ASPECT        = $Aspect.ToString($inv)
    $env:YL_MIN_JACOBIAN  = $MinJacobian.ToString($inv)
    $env:YL_MAX_WARPAGE   = $MaxWarpage.ToString($inv)
    $env:YL_MAX_SKEW      = $MaxSkew.ToString($inv)
    $env:YL_QUAD_MODE     = $QuadMode
    $env:YL_QUAD_FALLBACK = "$QuadFallback"

    $log = Join-Path $LogDir 'batch_log.txt'
    $csv = Join-Path $LogDir 'batch_summary.csv'

    function Invoke-HmBatchRun([string[]]$lines, [string]$engineVersion, [string]$consoleName) {
        $listFile = Join-Path $LogDir '_models.txt'
        [IO.File]::WriteAllLines($listFile, [string[]]@($lines | ForEach-Object { ($_ -split "`t")[0] }), $utf8)
        $env:YL_LIST = $listFile
        $jobRun = Join-Path $LogDir '_job_run.txt'
        [IO.File]::WriteAllLines($jobRun, [string[]]$lines, $utf8)
        $env:YL_JOB = $jobRun
        $env:YL_VERSION = $engineVersion
        Push-Location $LogDir
        try {
            & $HmBatch -tcl (Join-Path $tclDir 'run_batch.tcl') | Out-File -FilePath (Join-Path $LogDir $consoleName) -Encoding utf8
        } finally {
            Pop-Location
        }
        if (Test-Path -LiteralPath $csv) { return @(Import-Csv -LiteralPath $csv -Encoding UTF8) }
        return @()
    }

    $primaryEngine = if ($DoHex -eq 1) { 'H1' } else { 'H2' }
    $rows = @(Invoke-HmBatchRun $runLines $primaryEngine 'hmbatch_console.txt')
    $logText = ''
    if (Test-Path -LiteralPath $log) { $logText = [IO.File]::ReadAllText($log, [Text.Encoding]::UTF8) }

    # 3D SolidMap 실패(Unmappable 등) 모델 -> 옵션 켜져 있으면 새 hmbatch 세션에서 H2(2D Quad 스킨)로 대체 저장
    if ($DoHex -eq 1 -and $QuadFallback -eq 1) {
        $fallbackRows = @($rows | Where-Object { $_.PSObject.Properties['fallback_h2'] -and "$($_.fallback_h2)".Trim() -eq '1' })
        if ($fallbackRows.Count -gt 0) {
            $names = @($fallbackRows | ForEach-Object { $_.model })
            $fbLines = @($runLines | Where-Object { $names -contains [IO.Path]::GetFileNameWithoutExtension((($_ -split "`t")[0]).Trim().Trim('"')) })
            Write-Host "H2 (2D Quad 준비용) 대체 엔진 : $($fbLines.Count)개 모델 - $($names -join ', ')" -ForegroundColor Yellow
            $header = $null
            if (Test-Path -LiteralPath $csv) { $header = ([IO.File]::ReadAllLines($csv, [Text.Encoding]::UTF8)[0]).TrimStart([char]0xFEFF) }
            $h2Rows = @(Invoke-HmBatchRun $fbLines 'H2' 'hmbatch_console_h2.txt')
            if (Test-Path -LiteralPath $log) { $logText += "`r`n=== H2 fallback (2D Quad skin, new hmbatch process) ===`r`n" + [IO.File]::ReadAllText($log, [Text.Encoding]::UTF8) }
            $merged = foreach ($r in $rows) {
                $h2 = $h2Rows | Where-Object { $_.model -eq $r.model } | Select-Object -First 1
                if ($names -contains $r.model -and $h2) {
                    $h2.engine = 'H1>H2'
                    if ($h2.PSObject.Properties['mesher']) { $h2.mesher = 'H2 Quad fallback' }
                    if ($h2.PSObject.Properties['fallback_h2']) { $h2.fallback_h2 = '1' }
                    $h2
                } else { $r }
            }
            $rows = @($merged)
            $cols = if ($header) { @($header -split ',') } else { @($rows[0].PSObject.Properties.Name) }
            $outLines = @($cols -join ',') + @($rows | Select-Object -Property $cols | ConvertTo-Csv -NoTypeInformation | Select-Object -Skip 1)
            [IO.File]::WriteAllLines($csv, [string[]]$outLines, (New-Object Text.UTF8Encoding $true))
            [IO.File]::WriteAllText($log, $logText, $utf8)
        }
    }

    # 출력 폴더 정리
    $generated = @('batch_log.txt', 'batch_summary.csv', 'command.tcl', 'ct_reader.msg', 'hmmenu.set',
                   '_models.txt', '_job.txt', '_job_run.txt')
    Get-ChildItem -LiteralPath $OutDir -File | Where-Object {
        $generated -contains $_.Name -or $_.Name -like 'hmbatch_console*.txt' -or $_.Name -like '_ylh[0-9]_*' -or $_.Name -like '*_ylh[0-9]_*_res.txt' -or $_.Name -eq 'time_limit.txt'
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
    $rows | Format-Table model, engine, status, solids, unmap_solids, n_hex, n_penta, n_quad, bad_jac, bad_warp, jac_min, seconds -AutoSize | Out-String -Width 260 | Write-Host
    if ($rows.Count -lt $all.Count) {
        Write-Warning "$($all.Count)개 중 $($rows.Count)개만 처리됨 - hmbatch 가 중간에 멈췄습니다 (_logs\hmbatch_console.txt, _logs\batch_log.txt 확인)"
        return 1
    }
    $fail = @($rows | Where-Object { $_.status -ne 'OK' }).Count
    if ($fail -gt 0) { Write-Warning "$fail 개 모델 실패 (Unmappable 솔리드는 CAD/HM에서 Solid Split 후 재실행 권장)"; return 1 }
    return 0
}

# ---------------- main : settings window -> run ----------------
function Start-YL {
    $cfgDir       = Join-Path $env:LOCALAPPDATA 'YL_HEX_V1'
    $settingsFile = Join-Path $cfgDir 'YL_settings.json'
    $cfg = @{
        ElemSize = 2.0; Aspect = 5.0; MinJacobian = 0.50; MaxWarpage = 20.0; MaxSkew = 60.0
        DoHex = 1; QuadMode = 'mixed'; ElemType = 'C3D8H'; ExportInp = $false; QuadFallback = $true
        NameMode = 'suffix'; Suffix = '_HEX'; AddInfo = $false
        OutDir = (Join-Path $here 'batch_out_hex'); LastFolder = $here
    }
    if (Test-Path -LiteralPath $settingsFile) {
        try {
            $saved = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($k in @($cfg.Keys)) { if ($null -ne $saved.$k) { $cfg[$k] = $saved.$k } }
        } catch { }
    }

    if ($env:YL_JOB_FILE) {
        $outDir = if ($env:YL_OUT) { $env:YL_OUT } else { [string]$cfg.OutDir }
        return (Invoke-YLBatch -JobFile $env:YL_JOB_FILE `
            -ElemSize ([double]$cfg.ElemSize) -DoHex ([int]$cfg.DoHex) `
            -Aspect ([double]$cfg.Aspect) -MinJacobian ([double]$cfg.MinJacobian) `
            -MaxWarpage ([double]$cfg.MaxWarpage) -MaxSkew ([double]$cfg.MaxSkew) `
            -QuadMode ([string]$cfg.QuadMode) -ElemType ([string]$cfg.ElemType) `
            -ExportInp ([int][bool]$cfg.ExportInp) -QuadFallback ([int][bool]$cfg.QuadFallback) `
            -OutDir $outDir)
    }

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()

    $font = New-Object Drawing.Font('Malgun Gothic', 9)
    $form = New-Object Windows.Forms.Form
    $form.Text = 'YL Mesh HEX V1 (3D SolidMap Hexa / 2D Quad)'
    $form.Font = $font
    $form.ClientSize = New-Object Drawing.Size(880, 610)
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
    $gbModels  = New-Ctl GroupBox '① 모델 (Mappable 솔리드 포함 STP/HM 권장)' 12 8 856 260 $form
    $btnFiles  = New-Ctl Button '파일 추가...'  12 22 100 26 $gbModels
    $btnFolder = New-Ctl Button '폴더 추가...' 118 22 100 26 $gbModels
    $btnRemove = New-Ctl Button '선택 제거'    224 22 100 26 $gbModels
    $btnClear  = New-Ctl Button '모두 제거'    330 22 100 26 $gbModels
    $lblCount  = New-Ctl Label  ''             440 27 400 20 $gbModels

    $grid = New-Object Windows.Forms.DataGridView
    $grid.Location = New-Object Drawing.Point(12, 54)
    $grid.Size = New-Object Drawing.Size(832, 196)
    $grid.AllowUserToAddRows = $false
    $grid.AllowUserToResizeRows = $false
    $grid.RowHeadersVisible = $false
    $grid.SelectionMode = 'FullRowSelect'
    $grid.EditMode = 'EditOnEnter'
    $grid.BackgroundColor = [Drawing.SystemColors]::Window
    $colPath = New-Object Windows.Forms.DataGridViewTextBoxColumn; $colPath.Name = 'Path'; $colPath.Visible = $false
    $colSrc  = New-Object Windows.Forms.DataGridViewTextBoxColumn; $colSrc.Name = 'Src'; $colSrc.HeaderText = '원본 모델'; $colSrc.ReadOnly = $true; $colSrc.Width = 390
    $colOut  = New-Object Windows.Forms.DataGridViewTextBoxColumn; $colOut.Name = 'Out'; $colOut.HeaderText = '출력 이름 (.hm)'; $colOut.Width = 420
    [void]$grid.Columns.AddRange($colPath, $colSrc, $colOut)
    $gbModels.Controls.Add($grid)

    # ② Hexa mesh settings
    $gbMesh = New-Ctl GroupBox '② 핵사(Hexa) / 쿼드(Quad) 메시 설정' 12 274 856 135 $form

    $rbModeHex  = New-Ctl RadioButton '3D Hexa (Multi-Solid Map 자동 생성)' 14 22 260 22 $gbMesh
    $rbModeQuad = New-Ctl RadioButton '2D Quad/Mixed 스킨만 (수동 SolidMap 준비용)' 285 22 300 22 $gbMesh

    [void](New-Ctl Label '요소 크기 (mm)' 14 54 94 20 $gbMesh)
    $numSize = New-Ctl NumericUpDown $null 108 51 68 24 $gbMesh
    $numSize.DecimalPlaces = 2; $numSize.Minimum = 0.1; $numSize.Maximum = 100; $numSize.Increment = 0.5

    [void](New-Ctl Label 'Jacobian 최소' 190 54 88 20 $gbMesh)
    $numJac = New-Ctl NumericUpDown $null 280 51 64 24 $gbMesh
    $numJac.DecimalPlaces = 2; $numJac.Minimum = 0.05; $numJac.Maximum = 0.95; $numJac.Increment = 0.05

    [void](New-Ctl Label 'Warpage 한계(°)' 358 54 98 20 $gbMesh)
    $numWarp = New-Ctl NumericUpDown $null 458 51 64 24 $gbMesh
    $numWarp.DecimalPlaces = 1; $numWarp.Minimum = 5.0; $numWarp.Maximum = 60.0; $numWarp.Increment = 5.0

    [void](New-Ctl Label 'Aspect 한계' 536 54 78 20 $gbMesh)
    $numAsp = New-Ctl NumericUpDown $null 616 51 64 24 $gbMesh
    $numAsp.DecimalPlaces = 1; $numAsp.Minimum = 1.5; $numAsp.Maximum = 50.0; $numAsp.Increment = 0.5

    [void](New-Ctl Label 'Skew 한계(°)' 692 54 82 20 $gbMesh)
    $numSkew = New-Ctl NumericUpDown $null 776 51 64 24 $gbMesh
    $numSkew.DecimalPlaces = 1; $numSkew.Minimum = 10.0; $numSkew.Maximum = 85.0; $numSkew.Increment = 5.0

    [void](New-Ctl Label 'Abaqus 요소' 14 92 84 20 $gbMesh)
    $cmbElem = New-Ctl ComboBox $null 102 88 110 24 $gbMesh
    $cmbElem.DropDownStyle = 'DropDownList'
    [void]$cmbElem.Items.AddRange(@('C3D8H', 'C3D8RH', 'C3D8', 'C3D8R', 'C3D8I', 'C3D20H', 'C3D20RH', 'C3D20', 'C3D20R'))

    [void](New-Ctl Label '2D 소스 방식' 230 92 84 20 $gbMesh)
    $cmbQuad = New-Ctl ComboBox $null 316 88 115 24 $gbMesh
    $cmbQuad.DropDownStyle = 'DropDownList'
    [void]$cmbQuad.Items.AddRange(@('mixed', 'quads'))

    $chkInp      = New-Ctl CheckBox 'Abaqus .inp 자동 출력' 455 90 160 22 $gbMesh
    $chkFallback = New-Ctl CheckBox 'Unmappable 시 2D Quad 스킨으로 대체 저장' 620 90 230 22 $gbMesh

    # ③ naming
    $gbName = New-Ctl GroupBox '③ 출력 모델 이름' 12 415 856 90 $form
    $rbNameSame   = New-Ctl RadioButton '원래 이름 그대로' 14 22 140 22 $gbName
    $rbNameSuffix = New-Ctl RadioButton '원래 이름 + 접미사' 14 44 140 22 $gbName
    $txtSuffix    = New-Ctl TextBox $null 158 43 130 24 $gbName
    $rbNameCustom = New-Ctl RadioButton '모델별로 직접 입력 (위 표의 "출력 이름" 열을 고침)' 14 66 360 22 $gbName
    $chkInfo      = New-Ctl CheckBox '메시 정보 붙이기 (예: _C3D8H_2.0mm)' 430 22 330 22 $gbName

    # ④ output
    $gbOut = New-Ctl GroupBox '④ 출력 폴더' 12 511 640 54 $form
    $txtOut    = New-Ctl TextBox $null 14 22 540 24 $gbOut
    $btnBrowse = New-Ctl Button '찾기...' 560 21 70 26 $gbOut

    $btnRun    = New-Ctl Button '실행' 672 527 95 30 $form
    $btnCancel = New-Ctl Button '취소' 773 527 95 30 $form
    $form.CancelButton = $btnCancel
    $btnRun.Font = New-Object Drawing.Font('Malgun Gothic', 9, [Drawing.FontStyle]::Bold)

    # initial values
    if ([int]$cfg.DoHex -eq 1) { $rbModeHex.Checked = $true } else { $rbModeQuad.Checked = $true }
    $numSize.Value = [decimal][Math]::Min(100, [Math]::Max(0.1, [double]$cfg.ElemSize))
    $numJac.Value  = [decimal][Math]::Min(0.95, [Math]::Max(0.05, [double]$cfg.MinJacobian))
    $numWarp.Value = [decimal][Math]::Min(60, [Math]::Max(5, [double]$cfg.MaxWarpage))
    $numAsp.Value  = [decimal][Math]::Min(50, [Math]::Max(1.5, [double]$cfg.Aspect))
    $numSkew.Value = [decimal][Math]::Min(85, [Math]::Max(10, [double]$cfg.MaxSkew))
    $cmbElem.SelectedItem = if ($cmbElem.Items.Contains([string]$cfg.ElemType)) { [string]$cfg.ElemType } else { 'C3D8H' }
    $cmbQuad.SelectedItem = if ($cmbQuad.Items.Contains([string]$cfg.QuadMode)) { [string]$cfg.QuadMode } else { 'mixed' }
    $chkInp.Checked      = [bool]$cfg.ExportInp
    $chkFallback.Checked = [bool]$cfg.QuadFallback
    switch ($cfg.NameMode) { 'same' { $rbNameSame.Checked = $true } 'custom' { $rbNameCustom.Checked = $true } default { $rbNameSuffix.Checked = $true } }
    $txtSuffix.Text  = [string]$cfg.Suffix
    $chkInfo.Checked = [bool]$cfg.AddInfo
    $txtOut.Text     = [string]$cfg.OutDir

    $script:busy = $false
    $script:result = $null

    function Get-AutoName($row) {
        $name = [IO.Path]::GetFileNameWithoutExtension([string]$row.Cells['Path'].Value)
        if ($rbNameSuffix.Checked) { $name += $txtSuffix.Text.Trim() }
        if ($chkInfo.Checked) {
            $tag = if ($rbModeHex.Checked) { '_' + [string]$cmbElem.SelectedItem } else { '_2DQuad' }
            $name += $tag + '_' + ([double]$numSize.Value).ToString('0.0##', $inv) + 'mm'
        }
        return $name
    }

    function Update-View {
        if ($script:busy) { return }
        $script:busy = $true
        try {
            $isHex = $rbModeHex.Checked
            $cmbElem.Enabled     = $isHex
            $chkInp.Enabled      = $isHex
            $chkFallback.Enabled = $isHex
            $txtSuffix.Enabled   = $rbNameSuffix.Checked
            $chkInfo.Enabled     = -not $rbNameCustom.Checked
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
            [void][Windows.Forms.MessageBox]::Show("출력 이름을 확인하세요 (붉게 표시된 $($script:nameProblems)개 행).", $form.Text)
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
            $ans = [Windows.Forms.MessageBox]::Show("출력 폴더에 같은 이름의 파일이 이미 있습니다. 덮어쓸까요?`n`n$shown", $form.Text, 'YesNo', 'Warning')
            if ($ans -ne 'Yes') { return }
        }
        $jobRows = @()
        foreach ($row in $grid.Rows) {
            $jobRows += ([string]$row.Cells['Path'].Value) + "`t" + ([string]$row.Cells['Out'].Value) + "`t" + ([string]$cmbElem.SelectedItem)
        }
        $script:result = @{
            Job = $jobRows; OutDir = $outDir
            ElemSize = [double]$numSize.Value; DoHex = if ($rbModeHex.Checked) { 1 } else { 0 }
            Aspect = [double]$numAsp.Value; MinJacobian = [double]$numJac.Value
            MaxWarpage = [double]$numWarp.Value; MaxSkew = [double]$numSkew.Value
            QuadMode = [string]$cmbQuad.SelectedItem; ElemType = [string]$cmbElem.SelectedItem
            ExportInp = if ($chkInp.Checked -and $rbModeHex.Checked) { 1 } else { 0 }
            QuadFallback = if ($chkFallback.Checked) { 1 } else { 0 }
        }
        $cfg.ElemSize = $script:result.ElemSize; $cfg.DoHex = $script:result.DoHex
        $cfg.Aspect = $script:result.Aspect; $cfg.MinJacobian = $script:result.MinJacobian
        $cfg.MaxWarpage = $script:result.MaxWarpage; $cfg.MaxSkew = $script:result.MaxSkew
        $cfg.QuadMode = $script:result.QuadMode; $cfg.ElemType = $script:result.ElemType
        $cfg.ExportInp = [bool]$chkInp.Checked; $cfg.QuadFallback = [bool]$chkFallback.Checked
        $cfg.NameMode = if ($rbNameSame.Checked) { 'same' } elseif ($rbNameCustom.Checked) { 'custom' } else { 'suffix' }
        $cfg.Suffix = $txtSuffix.Text.Trim(); $cfg.AddInfo = [bool]$chkInfo.Checked; $cfg.OutDir = $outDir
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
        if ($d.ShowDialog($form) -eq 'OK') { $cfg.LastFolder = [IO.Path]::GetDirectoryName($d.FileNames[0]); Add-Models $d.FileNames }
    })
    $btnFolder.Add_Click({
        $d = New-Object Windows.Forms.FolderBrowserDialog
        $d.Description = '이 폴더와 하위 폴더의 STP/HM 파일을 추가합니다.'
        if ($cfg.LastFolder -and (Test-Path -LiteralPath $cfg.LastFolder)) { $d.SelectedPath = $cfg.LastFolder }
        if ($d.ShowDialog($form) -eq 'OK') {
            $cfg.LastFolder = $d.SelectedPath
            $found = @(Get-ChildItem -LiteralPath $d.SelectedPath -Recurse -File | Where-Object { $_.Extension -match '^\.(stp|step|hm)$' } | ForEach-Object { $_.FullName })
            if ($found.Count -eq 0) { [void][Windows.Forms.MessageBox]::Show('STP/HM 파일이 없습니다.', $form.Text) } else { Add-Models $found }
        }
    })
    $btnRemove.Add_Click({ foreach ($r in @($grid.SelectedRows)) { $grid.Rows.Remove($r) }; Update-View })
    $btnClear.Add_Click({ $grid.Rows.Clear(); Update-View })
    $btnBrowse.Add_Click({
        $d = New-Object Windows.Forms.FolderBrowserDialog
        if (Test-Path -LiteralPath $txtOut.Text) { $d.SelectedPath = $txtOut.Text }
        if ($d.ShowDialog($form) -eq 'OK') { $txtOut.Text = $d.SelectedPath }
    })
    foreach ($c in @($rbModeHex, $rbModeQuad, $rbNameSame, $rbNameSuffix, $rbNameCustom)) { $c.Add_CheckedChanged({ Update-View }) }
    $chkInfo.Add_CheckedChanged({ Update-View })
    $txtSuffix.Add_TextChanged({ Update-View })
    $numSize.Add_ValueChanged({ Update-View })
    $cmbElem.Add_SelectedIndexChanged({ Update-View })
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
    Write-Host "모델 $($r.Job.Count)개를 hmbatch(Hexa 엔진)로 처리합니다. 창을 닫지 마세요..."
    $code = Invoke-YLBatch -JobFile $jobFile -ElemSize $r.ElemSize -DoHex $r.DoHex `
        -Aspect $r.Aspect -MinJacobian $r.MinJacobian -MaxWarpage $r.MaxWarpage -MaxSkew $r.MaxSkew `
        -QuadMode $r.QuadMode -ElemType $r.ElemType -ExportInp $r.ExportInp -QuadFallback $r.QuadFallback -OutDir $r.OutDir
    Write-Host ''
    Write-Host "결과 폴더: $($r.OutDir)  (*.hm, _logs\batch_summary.csv / batch_log.txt)"
    return $code
}

# =====================================================================
# HyperMesh Hexa engine files (Tcl)
# =====================================================================
$YLEngineFiles = [ordered]@{}

# ----- run_batch.tcl -----
$YLEngineFiles['run_batch.tcl'] = @'
# =====================================================================
# run_batch.tcl : hmbatch driver for YL_H1 (3D Hexa) / YL_H2 (2D Quad)
# =====================================================================
set YLB_HERE [file dirname [file normalize [info script]]]

proc ylb_env {name default} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $default
}

set YLB_VERSION [string toupper [string trim [ylb_env YL_VERSION "H1"]]]
set YLB_ENGINE  [file join $YLB_HERE "YL_${YLB_VERSION}_batch.tcl"]
set YLB_LIST    [ylb_env YL_LIST    [file join $YLB_HERE "models.txt"]]
set YLB_OUT_DIR [file normalize [ylb_env YL_OUT_DIR [file join $YLB_HERE "batch_out_hex"]]]
file mkdir $YLB_OUT_DIR
set YLB_LOG_DIR [file normalize [ylb_env YL_LOG_DIR $YLB_OUT_DIR]]
file mkdir $YLB_LOG_DIR

set YLB_LOG [open [file join $YLB_LOG_DIR "batch_log.txt"] w]
fconfigure $YLB_LOG -encoding utf-8
proc ylb_log {txt} { puts $::YLB_LOG $txt; flush $::YLB_LOG }

proc ylb_reset_model {} {
    catch { hm_answernext yes }
    if {[catch { *deletemodel } err]} { ylb_log "  WARN: *deletemodel failed: $err" }
}

proc ylb_load_model {path} {
    set ext [string tolower [file extension $path]]
    if {$ext eq ".hm"} {
        catch { hm_answernext yes }
        *readfile $path
    } else {
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
        set line [string trim [string trimleft $line "\ufeff"] " \r\n"]
        if {[string trim $line] eq "" || [string index $line 0] eq "#"} { continue }
        set f [split $line "\t"]
        set p [file normalize [string trim [string trim [lindex $f 0]] "\""]]
        lappend YLB_MODELS $p
        set YLB_OUTNAME([llength $YLB_MODELS]) [string trim [lindex $f 1]]
        set YLB_ETYPE([llength $YLB_MODELS])   [string trim [lindex $f 2]]
    }
    close $fh
}

ylb_log "YL HEX batch start : [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
ylb_log "  engine  : $YLB_VERSION ($YLB_ENGINE)"
ylb_log "  out dir : $YLB_OUT_DIR"
ylb_log "  models  : [llength $YLB_MODELS]"

set YLB_CSV [open [file join $YLB_LOG_DIR "batch_summary.csv"] w]
fconfigure $YLB_CSV -encoding utf-8
puts -nonewline $YLB_CSV "\ufeff"
puts $YLB_CSV "model,status,engine,mesher,elem_size,elem_type,solids,unmap_solids,surfs,n_hex,n_penta,n_quad,n_tria,bad_jac,bad_warp,bad_asp,bad_skew,neg_jac,jac_min,jac_mean,warp_max,asp_p95,smoothed_nodes,pass_hex,fallback_h2,seconds,output,inp,note"

set YLB_NOK 0; set YLB_NFAIL 0; set YLB_IDX 0

foreach YLB_MODEL $YLB_MODELS {
    incr YLB_IDX
    set t0 [clock seconds]
    set name [file rootname [file tail $YLB_MODEL]]
    ylb_log ""
    ylb_log "\[$YLB_IDX/[llength $YLB_MODELS]\] $YLB_MODEL"

    set ::YL_BATCH         1
    set ::YL_ELEM_SIZE     [ylb_env YL_ELEM_SIZE 2.0]
    set ::YL_DO_HEX        [ylb_env YL_DO_HEX 1]
    set ::YL_ELEM_TYPE     [ylb_env YL_ELEM_TYPE "C3D8H"]
    set ::YL_ASPECT        [ylb_env YL_ASPECT 5.0]
    set ::YL_MIN_JACOBIAN  [ylb_env YL_MIN_JACOBIAN 0.50]
    set ::YL_MAX_WARPAGE   [ylb_env YL_MAX_WARPAGE 20.0]
    set ::YL_MAX_SKEW      [ylb_env YL_MAX_SKEW 60.0]
    set ::YL_QUAD_MODE     [ylb_env YL_QUAD_MODE "mixed"]
    set ::YL_QUAD_FALLBACK [ylb_env YL_QUAD_FALLBACK 1]
    set ::YL_EXPORT_INP    [ylb_env YL_EXPORT_INP 0]
    set ::YL_OUT_DIR       $YLB_OUT_DIR
    set ::YL_LOG_DIR       $YLB_LOG_DIR
    set ::YL_BASE_NAME     $name
    set ::YL_OUT_NAME      $YLB_OUTNAME($YLB_IDX)
    if {$YLB_ETYPE($YLB_IDX) ne ""} { set ::YL_ELEM_TYPE $YLB_ETYPE($YLB_IDX) }
    set ::YL_RESULT [list status "FAIL" msg "engine did not run"]

    if {![file exists $YLB_MODEL]} {
        set ::YL_RESULT [list status "FAIL" msg "file not found"]
    } else {
        ylb_reset_model
        if {[catch { ylb_load_model $YLB_MODEL } err]} {
            set ::YL_RESULT [list status "FAIL" msg "import failed: $err"]
        } else {
            ylb_log "  imported: [ylb_count solids] solids, [ylb_count surfs] surfs, [ylb_count comps] comps"
            if {[catch { uplevel #0 [list source $YLB_ENGINE] } err]} {
                set ::YL_RESULT [list status "FAIL" msg "script error: $err"]
                ylb_log "  --- errorInfo ---\n$::errorInfo\n  -----------------"
            }
        }
    }

    array unset R
    array set R {status FAIL engine "" mesher "" size "" etype "" solids "" unmap_solids "" surfs "" n_hex 0 n_penta 0 n_quad 0 n_tria 0 bad_jac "" bad_warp "" bad_asp "" bad_skew "" neg_jac "" jac_min "" jac_mean "" warp_max "" asp_p95 "" smoothed_nodes 0 pass_hex 0 fallback_h2 0 out "" inp "" msg ""}
    set R(engine) $YLB_VERSION
    array set R $::YL_RESULT
    set dt [expr {[clock seconds] - $t0}]
    if {$R(status) eq "OK"} { incr YLB_NOK } else { incr YLB_NFAIL }

    ylb_log "  => $R(status) (${dt}s)  hex=$R(n_hex) penta=$R(n_penta) quad=$R(n_quad)  pass=$R(pass_hex)"
    foreach ln [split $R(msg) "\n"] { if {$ln ne ""} { ylb_log "     $ln" } }

    set note [string map [list "\n" " | " "," ";" "\"" "'"] [expr {$R(status) eq "OK" ? "" : $R(msg)}]]
    puts $YLB_CSV "\"$name\",$R(status),$R(engine),$R(mesher),$R(size),$R(etype),$R(solids),$R(unmap_solids),$R(surfs),$R(n_hex),$R(n_penta),$R(n_quad),$R(n_tria),$R(bad_jac),$R(bad_warp),$R(bad_asp),$R(bad_skew),$R(neg_jac),$R(jac_min),$R(jac_mean),$R(warp_max),$R(asp_p95),$R(smoothed_nodes),$R(pass_hex),$R(fallback_h2),$dt,\"$R(out)\",\"$R(inp)\",\"$note\""
    flush $YLB_CSV
}

ylb_log ""
ylb_log "DONE : OK=$YLB_NOK  FAIL=$YLB_NFAIL  ([clock format [clock seconds] -format {%H:%M:%S}])"
close $YLB_CSV
close $YLB_LOG
exit [expr {$YLB_NFAIL > 0 ? 1 : 0}]
'@

# ----- YL_HEX_qclean.tcl -----
$YLEngineFiles['YL_HEX_qclean.tcl'] = @'
# =====================================================================
# YL_HEX_qclean.tcl : Topology-preserving 3D Hex8 interior node smoother
#   - Tria용 YL_V8_qclean(collapse/flip/split)과 달리 연결성을 유지하며
#     내부 절점만 품질 검증(Scaled Jacobian 개선) 후 이동시킵니다.
# =====================================================================
namespace eval hqc {
    variable X; variable H; variable NH; variable BND
}

proc hqc::sub {a b} {
    list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}]
}
proc hqc::dot {a b} {
    expr {[lindex $a 0]*[lindex $b 0] + [lindex $a 1]*[lindex $b 1] + [lindex $a 2]*[lindex $b 2]}
}
proc hqc::cross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc hqc::len {a} { expr {sqrt([dot $a $a])} }

# Hex8 8개 코너의 Scaled Jacobian 최솟값 (정육면체 = 1.0, 반전 요소 <= 0.0)
proc hqc::hex_min_jac {pts} {
    lassign $pts p0 p1 p2 p3 p4 p5 p6 p7
    set corners [list \
        [list $p0 $p1 $p3 $p4] [list $p1 $p2 $p0 $p5] [list $p2 $p3 $p1 $p6] [list $p3 $p0 $p2 $p7] \
        [list $p4 $p7 $p5 $p0] [list $p5 $p4 $p6 $p1] [list $p6 $p5 $p7 $p2] [list $p7 $p6 $p4 $p3]]
    set jmin 1e9
    foreach c $corners {
        lassign $c o a b d
        set e1 [sub $a $o]; set e2 [sub $b $o]; set e3 [sub $d $o]
        set l1 [len $e1]; set l2 [len $e2]; set l3 [len $e3]
        if {$l1 < 1e-12 || $l2 < 1e-12 || $l3 < 1e-12} { return -1.0 }
        set det [dot [cross $e1 $e2] $e3]
        set sj  [expr {$det / ($l1 * $l2 * $l3)}]
        if {$sj < $jmin} { set jmin $sj }
    }
    return $jmin
}

proc hqc::elem_jac {e} {
    variable X; variable H
    set pts {}
    foreach n $H($e) { lappend pts $X($n) }
    return [hex_min_jac $pts]
}

proc hqc::smooth_hex {min_jac {passes 5}} {
    variable X; variable H; variable NH; variable BND
    array unset X; array unset H; array unset NH; array unset BND

    *clearmark elems 1
    *createmark elems 1 "by config" hex8
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    if {[llength $eids] == 0} { return 0 }

    # 1. Hex8 절점 및 면(Face) 공유 횟수로 외곽 경계 절점 판별
    array set face_cnt {}
    set face_idx {{0 1 2 3} {4 5 6 7} {0 1 5 4} {1 2 6 5} {2 3 7 6} {3 0 4 7}}
    foreach e $eids {
        set nds [lrange [hm_nodelist $e] 0 7]
        if {[llength $nds] != 8} { continue }
        set H($e) $nds
        foreach n $nds {
            lappend NH($n) $e
            if {![info exists X($n)]} { set X($n) [lindex [hm_nodevalue $n] 0] }
        }
        foreach fi $face_idx {
            set fnds {}
            foreach idx $fi { lappend fnds [lindex $nds $idx] }
            set key [join [lsort -integer $fnds] ","]
            if {[info exists face_cnt($key)]} { incr face_cnt($key) } else { set face_cnt($key) 1 }
        }
    }

    # 면 공유가 1회뿐인 면 = 외곽 경계면 -> 해당 절점은 CAD 형상 유지를 위해 이동 금지
    foreach k [array names face_cnt] {
        if {$face_cnt($k) == 1} {
            foreach n [split $k ","] { set BND($n) 1 }
        }
    }
    # Penta6 등 비-Hex 요소와 공유되는 절점도 고정
    *clearmark elems 1
    catch { *createmark elems 1 "by config" penta6 pyramid5 tetra4 }
    foreach e [hm_getmark elems 1] {
        foreach n [hm_nodelist $e] { set BND($n) 1 }
    }
    *clearmark elems 1

    # 2. 불량 요소 인접 내부 절점만 선별하여 품질 검증 기반 스무딩
    array set moved {}
    for {set p 0} {$p < $passes} {incr p} {
        set cand {}
        foreach e [array names H] {
            if {[elem_jac $e] < $min_jac} {
                foreach n $H($e) {
                    if {![info exists BND($n)]} { set cand($n) 1 }
                }
            }
        }
        if {[array size cand] == 0} { break }
        set changed 0
        foreach n [array names cand] {
            set els $NH($n)
            # 인접 절점 수집 (같은 Hex 내 직접 연결된 이웃 절점)
            array unset nbrs
            foreach e $els {
                foreach m $H($e) { if {$m != $n} { set nbrs($m) 1 } }
            }
            set nb [array names nbrs]
            if {[llength $nb] < 4} { continue }
            set cx 0.0; set cy 0.0; set cz 0.0
            foreach m $nb {
                lassign $X($m) x y z
                set cx [expr {$cx + $x}]; set cy [expr {$cy + $y}]; set cz [expr {$cz + $z}]
            }
            set k [expr {double([llength $nb])}]
            set old $X($n)
            lassign $old ox oy oz
            # 감쇠 계수 0.5 (과도한 이동으로 인한 반전 방지)
            set trial [list [expr {0.5*($ox + $cx/$k)}] [expr {0.5*($oy + $cy/$k)}] [expr {0.5*($oz + $cz/$k)}]]

            set j_before 1e9
            foreach e $els { set j [elem_jac $e]; if {$j < $j_before} { set j_before $j } }
            set X($n) $trial
            set j_after 1e9
            foreach e $els { set j [elem_jac $e]; if {$j < $j_after} { set j_after $j } }

            if {$j_after > $j_before + 1e-4 && $j_after > 0.05} {
                set moved($n) 1
                incr changed
            } else {
                set X($n) $old
            }
        }
        array unset cand
        if {$changed == 0} { break }
    }

    set nm 0
    foreach n [array names moved] {
        lassign $X($n) x y z
        if {![catch { *nodemodify $n $x $y $z }]} { incr nm }
    }
    return $nm
}
'@

# ----- YL_HEX_stats.tcl -----
$YLEngineFiles['YL_HEX_stats.tcl'] = @'
# =====================================================================
# YL_HEX_stats.tcl : 2D Quad 및 3D Hex8 좌표 기반 독립 품질 통계
# =====================================================================
proc yhs_sub {a b} { list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}] }
proc yhs_dot {a b} { expr {[lindex $a 0]*[lindex $b 0]+[lindex $a 1]*[lindex $b 1]+[lindex $a 2]*[lindex $b 2]} }
proc yhs_cross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc yhs_len {a} { expr {sqrt([yhs_dot $a $a])} }
proc yhs_xyz {nd} {
    if {![info exists ::YHX($nd)]} { set ::YHX($nd) [lindex [hm_nodevalue $nd] 0] }
    return $::YHX($nd)
}
proc yhs_quant {sorted p} {
    set n [llength $sorted]
    if {$n == 0} { return 0.0 }
    return [lindex $sorted [expr {int(floor($p * ($n - 1)))}]]
}

# Quad 1개 면의 Warpage 각도 (deg): 두 대각선 분할 중 더 작은 이면각
proc yhs_quad_warp {p0 p1 p2 p3} {
    set rad [expr {180.0 / acos(-1.0)}]
    set w 180.0
    foreach {a b c d} [list $p0 $p1 $p2 $p3  $p1 $p2 $p3 $p0] {
        set n1 [yhs_cross [yhs_sub $b $a] [yhs_sub $c $a]]
        set n2 [yhs_cross [yhs_sub $c $a] [yhs_sub $d $a]]
        set l1 [yhs_len $n1]; set l2 [yhs_len $n2]
        if {$l1 < 1e-14 || $l2 < 1e-14} { continue }
        set cang [expr {max(-1.0, min(1.0, [yhs_dot $n1 $n2] / ($l1 * $l2)))}]
        set ang [expr {acos($cang) * $rad}]
        if {$ang < $w} { set w $ang }
    }
    return [expr {$w > 179.9 ? 0.0 : $w}]
}

proc yhs_hex_stats {elist {maxn 300000}} {
    set R [dict create n [llength $elist] neg_jac 0]
    if {[llength $elist] == 0} { return $R }
    set step 1
    if {[llength $elist] > $maxn} { set step [expr {int(ceil(double([llength $elist]) / $maxn))}] }

    set jacs {}; set warps {}; set asps {}
    set neg 0; set n 0; set i 0
    set edges {{0 1} {1 2} {2 3} {3 0} {4 5} {5 6} {6 7} {7 4} {0 4} {1 5} {2 6} {3 7}}
    set faces {{0 1 2 3} {4 5 6 7} {0 1 5 4} {1 2 6 5} {2 3 7 6} {3 0 4 7}}

    foreach e $elist {
        if {$i % $step} { incr i; continue }
        incr i
        set nds [lrange [hm_nodelist $e] 0 7]
        if {[llength $nds] < 8} { continue }
        set P {}
        foreach nd $nds { lappend P [yhs_xyz $nd] }
        set j [hqc::hex_min_jac $P]
        if {$j <= 0.0} { incr neg }
        set wmax 0.0
        foreach f $faces {
            set w [yhs_quad_warp [lindex $P [lindex $f 0]] [lindex $P [lindex $f 1]] [lindex $P [lindex $f 2]] [lindex $P [lindex $f 3]]]
            if {$w > $wmax} { set wmax $w }
        }
        set lmin 1e30; set lmax 0.0
        foreach ed $edges {
            set l [yhs_len [yhs_sub [lindex $P [lindex $ed 1]] [lindex $P [lindex $ed 0]]]]
            if {$l < $lmin} { set lmin $l }
            if {$l > $lmax} { set lmax $l }
        }
        set asp [expr {$lmin > 1e-12 ? $lmax / $lmin : 1e6}]
        lappend jacs $j; lappend warps $wmax; lappend asps $asp
        incr n
    }
    if {$n == 0} { return $R }
    set jacs  [lsort -real $jacs]
    set warps [lsort -real $warps]
    set asps  [lsort -real $asps]
    set sj 0.0; foreach x $jacs { set sj [expr {$sj + $x}] }
    dict set R sampled  $n
    dict set R neg_jac  $neg
    dict set R jac_min  [lindex $jacs 0]
    dict set R jac_mean [expr {$sj / $n}]
    dict set R warp_max [lindex $warps end]
    dict set R asp_p95  [yhs_quant $asps 0.95]
    return $R
}
'@

# ----- YL_H1_batch.tcl (Primary: 3D Multi-Solid Map Hexa) -----
$YLEngineFiles['YL_H1_batch.tcl'] = @'
# =====================================================================
# YL_H1_batch.tcl : 3D Hexa 자동화 엔진 (Multi-Solid Map -> 스무딩 -> 품질검증 -> Abaqus 타입/INP)
# =====================================================================
if {![info exists ::YL_BATCH]} { set ::YL_BATCH 0 }
set ::YL_RESULT [list status "FAIL" msg "not finished"]

set ELEM_SIZE        2.0
set TARGET_ASPECT    5.0
set MIN_JACOBIAN     0.50
set MAX_WARPAGE      20.0
set MAX_SKEW         60.0
set QUAD_MODE        "mixed"
set ABAQUS_ELEM_TYPE "C3D8H"
set DELETE_2D_SKIN   1
set QUAD_FALLBACK    1
set EXPORT_INP       0
set OUT_DIR          "C:/work/mesh_hex"
set BASE_NAME        "YL_H1"

if {[info exists ::YL_ELEM_SIZE] && $::YL_ELEM_SIZE > 0.05} { set ELEM_SIZE [expr {double($::YL_ELEM_SIZE)}] }
if {[info exists ::YL_ASPECT]}        { set TARGET_ASPECT    [expr {double($::YL_ASPECT)}] }
if {[info exists ::YL_MIN_JACOBIAN]}  { set MIN_JACOBIAN     [expr {double($::YL_MIN_JACOBIAN)}] }
if {[info exists ::YL_MAX_WARPAGE]}   { set MAX_WARPAGE      [expr {double($::YL_MAX_WARPAGE)}] }
if {[info exists ::YL_MAX_SKEW]}      { set MAX_SKEW         [expr {double($::YL_MAX_SKEW)}] }
if {[info exists ::YL_QUAD_MODE]}     { set QUAD_MODE        [string tolower [string trim $::YL_QUAD_MODE]] }
if {[info exists ::YL_ELEM_TYPE]}     { set ABAQUS_ELEM_TYPE [string toupper [string trim $::YL_ELEM_TYPE]] }
if {[info exists ::YL_QUAD_FALLBACK]} { set QUAD_FALLBACK    $::YL_QUAD_FALLBACK }
if {[info exists ::YL_EXPORT_INP]}    { set EXPORT_INP       $::YL_EXPORT_INP }
if {[info exists ::YL_OUT_DIR]}       { set OUT_DIR          $::YL_OUT_DIR }
if {[info exists ::YL_BASE_NAME]}     { set BASE_NAME        $::YL_BASE_NAME }
file mkdir $OUT_DIR

set ylh_DIR [file dirname [file normalize [info script]]]
source [file join $ylh_DIR "YL_HEX_qclean.tcl"]
source [file join $ylh_DIR "YL_HEX_stats.tcl"]

# ---------------- helpers ----------------
proc ylh_count_elems {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}
proc ylh_delete_elems {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
    *clearmark elems 1
}
proc ylh_solid_3d_count {sid} {
    *clearmark solids 1; *clearmark elems 2
    *createmark solids 1 $sid
    set n 0
    if {![catch { *findmark solids 1 257 1 elems 0 2 }]} { set n [llength [hm_getmark elems 2]] }
    *clearmark solids 1; *clearmark elems 2
    return $n
}

# Abaqus 템플릿(standard.3d)에서 {config type} 동적 파싱 (버전 차이 방지)
proc ylh_lookup_abaqus_type {ename tpl_path} {
    set ename [string toupper [string trim $ename]]
    if {[file exists $tpl_path]} {
        if {![catch {open $tpl_path r} fh]} {
            set txt [read $fh]
            close $fh
            set pat [format {\*elements\(\s*(\d+)\s*,\s*(\d+)\s*,\s*%s\b} $ename]
            if {[regexp -nocase $pat $txt -> cfg typ]} {
                return [list $cfg $typ]
            }
        }
    }
    # fallback 기본 테이블 (HM 2019 standard.3d 기준)
    array set fb {
        C3D8 {208 1} C3D8R {208 2} C3D8I {208 3} C3D8H {208 4} C3D8RH {208 5} C3D8IH {208 6}
        C3D20 {220 1} C3D20R {220 2} C3D20H {220 3} C3D20RH {220 4}
        C3D6 {206 1} C3D6H {206 2}
        C3D15 {215 1} C3D15H {215 2}
    }
    if {[info exists fb($ename)]} { return $fb($ename) }
    return {}
}

# Hex 요소 타입에 짝이 되는 Wedge(Penta) 요소명 자동 매핑
proc ylh_wedge_for_hex {hex_type} {
    switch -exact -- $hex_type {
        C3D8    { return "C3D6" }
        C3D8R   { return "C3D6" }
        C3D8I   { return "C3D6" }
        C3D8H   { return "C3D6H" }
        C3D8RH  { return "C3D6H" }
        C3D8IH  { return "C3D6H" }
        C3D20   { return "C3D15" }
        C3D20R  { return "C3D15" }
        C3D20H  { return "C3D15H" }
        C3D20RH { return "C3D15H" }
        default { return "C3D6H" }
    }
}

# ---------------- 1. 솔리드 확인 ----------------
*clearmark solids 1; *createmark solids 1 "all"
set solid_ids [hm_getmark solids 1]
*clearmark solids 1

*clearmark surfs 1; *createmark surfs 1 "all"
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1

set n_solids [llength $solid_ids]
set n_surfs  [llength $surf_ids]

if {$n_solids == 0} {
    set ::YL_RESULT [list status "FAIL" engine "H1" mesher "SolidMap" size $ELEM_SIZE etype $ABAQUS_ELEM_TYPE \
        solids 0 unmap_solids 0 surfs $n_surfs pass_hex 0 fallback_h2 $QUAD_FALLBACK \
        msg "솔리드가 없습니다(surfs=$n_surfs). 닫힌 Solid CAD가 필요하며, 2D Quad 준비 모드로 전환합니다."]
    return
}

# 기존 요소 정리
ylh_delete_elems {tria3 tria6 quad4 quad8 tetra4 tetra10 hex8 hex20 penta6 penta15 pyramid5 pyramid13}
catch { *elementorder 1 }

# ---------------- 2. Multi-Solid Map 실행 ----------------
# HM 2019 Multi-Solids 플래그:
#   mixed 기본 = 131074, quads 전용 = 131072 (환경변수 YL_SOLIDMAP_FLAGS 로 재정의 가능)
set sm_flags [expr {$QUAD_MODE eq "quads" ? 131072 : 131074}]
if {[info exists ::env(YL_SOLIDMAP_FLAGS)] && $::env(YL_SOLIDMAP_FLAGS) ne ""} {
    set sm_flags $::env(YL_SOLIDMAP_FLAGS)
}

set sm_err ""
*clearmark solids 1
eval *createmark solids 1 $solid_ids
if {[catch {
    *solidmap_solids_begin 1 $sm_flags $ELEM_SIZE
    *solidmap_solids_end
} err1]} {
    # 플래그 호환성 fallback (flags = 0)
    *clearmark solids 1
    eval *createmark solids 1 $solid_ids
    if {[catch {
        *solidmap_solids_begin 1 0 $ELEM_SIZE
        *solidmap_solids_end
    } err2]} {
        set sm_err "solidmap failed: $err1 / $err2"
    }
}
*clearmark solids 1

# 다중 솔리드 경계 절점 병합(Equivalence)
*clearmark elems 1
catch { *createmark elems 1 "by config" hex8 penta6 pyramid5 }
if {[llength [hm_getmark elems 1]] > 0} {
    catch { *equivalence elems 1 [expr {0.001 * $ELEM_SIZE}] 1 0 0 }
}
*clearmark elems 1

if {$DELETE_2D_SKIN} {
    ylh_delete_elems {tria3 tria6 quad4 quad8}
}

# Unmappable(미생성) 솔리드 판별
set unmap_ids {}
foreach s $solid_ids {
    if {[ylh_solid_3d_count $s] == 0} { lappend unmap_ids $s }
}
set n_unmap [llength $unmap_ids]
set n_hex   [ylh_count_elems {hex8 hex20}]
set n_penta [ylh_count_elems {penta6 penta15}]

# ---------------- 3. 3D Hex 내부 절점 스무딩 (위상 보존) ----------------
set n_smoothed 0
if {$n_hex > 0} {
    catch { set n_smoothed [hqc::smooth_hex $MIN_JACOBIAN 5] }
}

# ---------------- 4. 3D 품질 검사 (1차 요소 기준 측정) ----------------
set bad_jac -1; set bad_warp -1; set bad_asp -1; set bad_skew -1
if {$n_hex + $n_penta > 0} {
    foreach {var cmd lim lbl} [list \
        bad_jac  *elementtestjacobian $MIN_JACOBIAN "3D Jacobian" \
        bad_warp *elementtestwarpage  $MAX_WARPAGE  "3D Warpage" \
        bad_asp  *elementtestaspect   $TARGET_ASPECT "3D Aspect" \
        bad_skew *elementtestskew     $MAX_SKEW     "3D Skew"] {
        *clearmark elems 1; *clearmark elems 2
        *createmark elems 1 "by config" hex8 penta6
        if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} {
            set $var [llength [hm_getmark elems 2]]
        }
        *clearmark elems 1; *clearmark elems 2
    }
}

array unset ::YHX
*clearmark elems 1
*createmark elems 1 "by config" hex8
set hex_eids [hm_getmark elems 1]
*clearmark elems 1
set HS [yhs_hex_stats $hex_eids 300000]
array unset ::YHX

set neg_jac  [dict get $HS neg_jac]
set jac_min  [expr {[dict exists $HS jac_min]  ? [format %.3f [dict get $HS jac_min]]  : ""}]
set jac_mean [expr {[dict exists $HS jac_mean] ? [format %.3f [dict get $HS jac_mean]] : ""}]
set warp_max [expr {[dict exists $HS warp_max] ? [format %.2f [dict get $HS warp_max]] : ""}]
set asp_p95  [expr {[dict exists $HS asp_p95]  ? [format %.2f [dict get $HS asp_p95]]  : ""}]

# ---------------- 5. Abaqus 요소 차수/타입 적용 ----------------
set tpl_path "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d"
set type_err ""
set assigned_hex_type ""
if {$n_hex + $n_penta > 0} {
    catch { *templatefileset $tpl_path }
    set hex_pair [ylh_lookup_abaqus_type $ABAQUS_ELEM_TYPE $tpl_path]
    if {[llength $hex_pair] != 2} {
        set type_err "알 수 없는 Abaqus 요소 타입 '$ABAQUS_ELEM_TYPE'"
        set hex_pair {208 4}
    }
    lassign $hex_pair hex_cfg hex_typ
    set is_second [expr {$hex_cfg == 220}]

    if {$is_second} {
        *clearmark elems 1
        *createmark elems 1 "by config" hex8 penta6
        if {[llength [hm_getmark elems 1]] > 0} {
            # 2nd arg = 1 : 중간 절점을 CAD 곡면에 배치
            catch { *orderchangetosecond 1 1 }
        }
        *clearmark elems 1
    }

    # Hex 타입 지정
    *clearmark elems 1
    *createmark elems 1 "by config" [expr {$is_second ? "hex20" : "hex8"}]
    set e0 [lindex [hm_getmark elems 1] 0]
    if {$e0 ne ""} {
        catch { *elementtype $hex_cfg $hex_typ }
        catch { *elementsettypes 1 }
        catch { set assigned_hex_type [hm_getvalue elems id=$e0 dataname=typename] }
    }
    *clearmark elems 1

    # Penta(Wedge) 타입 자동 동시 지정 (C3D6 / C3D6H / C3D15 / C3D15H)
    if {$n_penta > 0} {
        set wname [ylh_wedge_for_hex $ABAQUS_ELEM_TYPE]
        set wpair [ylh_lookup_abaqus_type $wname $tpl_path]
        if {[llength $wpair] == 2} {
            lassign $wpair w_cfg w_typ
            *clearmark elems 1
            *createmark elems 1 "by config" [expr {$is_second ? "penta15" : "penta6"}]
            if {[llength [hm_getmark elems 1]] > 0} {
                catch { *elementtype $w_cfg $w_typ }
                catch { *elementsettypes 1 }
            }
            *clearmark elems 1
        }
    }
    if {$assigned_hex_type ne "" && $assigned_hex_type ne $ABAQUS_ELEM_TYPE} {
        set type_err "요소 타입 불일치: 실제='$assigned_hex_type', 요청='$ABAQUS_ELEM_TYPE'"
    }
}

set n_hex   [ylh_count_elems {hex8 hex20}]
set n_penta [ylh_count_elems {penta6 penta15}]

# ---------------- 6. 합격 판정 및 저장 / INP 출력 ----------------
set pass_hex [expr {$sm_err eq "" && $type_err eq "" && $n_hex > 0 && $n_unmap == 0 && $neg_jac == 0 && $bad_jac == 0 && $bad_warp == 0 && $bad_asp == 0 ? 1 : 0}]
set fallback_h2 [expr {($n_hex == 0 || $n_unmap > 0) && $QUAD_FALLBACK ? 1 : 0}]

set out_stem "${BASE_NAME}_HEX_${ABAQUS_ELEM_TYPE}_${ELEM_SIZE}mm"
if {[info exists ::YL_OUT_NAME] && [string trim $::YL_OUT_NAME] ne ""} { set out_stem [string trim $::YL_OUT_NAME] }
set out_hm "$OUT_DIR/${out_stem}.hm"
set out_inp ""
set inp_err ""

catch { hm_answernext yes }
set write_failed [catch { *writefile "$out_hm" 1 } write_err]

if {$EXPORT_INP && $n_hex > 0 && $neg_jac == 0} {
    set out_inp "$OUT_DIR/${out_stem}.inp"
    catch { file delete $out_inp }
    catch { hm_answernext yes }
    if {[catch {
        *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"
        *feoutputwithdata $tpl_path $out_inp 0 0 2 1 2
    } inp_err]} { set inp_err "INP EXPORT FAILED: $inp_err" } else { set inp_err "" }
}

set msg [format "YL_H1 (Multi-Solid Map Hexa) 완료\n- Size: %.2f mm, Type: %s (실제: %s), QuadMode: %s\n- Solids: %d (Unmappable: %d -> %s)\n- Elements: Hex=%d, Penta=%d (Smoothed interior nodes: %d)\n- Quality: BadJac(<%.2f)=%d, NegJac=%d, BadWarp(>%.1f)=%d, BadAsp(>%.1f)=%d, BadSkew(>%.1f)=%d\n- Stats: Jac min=%s mean=%s, Warp max=%s, Asp p95=%s\n- Saved: %s" \
    $ELEM_SIZE $ABAQUS_ELEM_TYPE $assigned_hex_type $QUAD_MODE \
    $n_solids $n_unmap [join [lrange $unmap_ids 0 9] ","] \
    $n_hex $n_penta $n_smoothed \
    $MIN_JACOBIAN $bad_jac $neg_jac $MAX_WARPAGE $bad_warp $TARGET_ASPECT $bad_asp $MAX_SKEW $bad_skew \
    $jac_min $jac_mean $warp_max $asp_p95 $out_hm]

set _status "OK"
set _why {}
if {$write_failed} { lappend _why "SAVE FAILED: $write_err" }
if {$sm_err ne ""} { lappend _why $sm_err }
if {$type_err ne ""} { lappend _why $type_err }
if {$inp_err ne ""} { lappend _why $inp_err }
if {$n_hex == 0} { lappend _why "NO HEX ELEMENTS CREATED (솔리드 분할 필요)" }
if {$n_unmap > 0} { lappend _why "$n_unmap SOLID(S) UNMAPPABLE (ID: [join [lrange $unmap_ids 0 9] ,])" }
if {$neg_jac > 0} { lappend _why "$neg_jac NEGATIVE JACOBIAN HEX(ES)" }
if {$bad_jac > 0} { lappend _why "$bad_jac HEX(ES) BELOW JACOBIAN $MIN_JACOBIAN" }
if {$bad_warp > 0} { lappend _why "$bad_warp HEX(ES) ABOVE WARPAGE $MAX_WARPAGE" }
if {$bad_asp > 0} { lappend _why "$bad_asp HEX(ES) ABOVE ASPECT $TARGET_ASPECT" }
if {[llength $_why] > 0} {
    set _status "FAIL"
    foreach w $_why { append msg "\n*** $w" }
}

set ::YL_RESULT [list status $_status engine "H1" mesher "SolidMap" size $ELEM_SIZE etype $ABAQUS_ELEM_TYPE \
    solids $n_solids unmap_solids $n_unmap surfs $n_surfs n_hex $n_hex n_penta $n_penta n_quad 0 n_tria 0 \
    bad_jac $bad_jac bad_warp $bad_warp bad_asp $bad_asp bad_skew $bad_skew neg_jac $neg_jac \
    jac_min $jac_min jac_mean $jac_mean warp_max $warp_max asp_p95 $asp_p95 smoothed_nodes $n_smoothed \
    pass_hex $pass_hex fallback_h2 $fallback_h2 out $out_hm inp $out_inp msg $msg]
'@

# ----- YL_H2_batch.tcl (2D Quad/Mixed BatchMesher Skin: 수동 SolidMap 준비 / Fallback) -----
$YLEngineFiles['YL_H2_batch.tcl'] = @'
# =====================================================================
# YL_H2_batch.tcl : BatchMesher 기반 2D Quad/Mixed 스킨 생성
#   - 2D 전용 모드 또는 H1(SolidMap)에서 Unmappable로 실패한 모델의 준비용
#   - 기존 YL_V9_batch.tcl 과 달리 element_type 2(mixed)/1(quads) 사용,
#     *splitelements(Quad->Tria 강제 분할) 제거, Quad 품질 지표 적용.
# =====================================================================
if {![info exists ::YL_BATCH]} { set ::YL_BATCH 0 }
set ::YL_RESULT [list status "FAIL" msg "not finished"]

set ELEM_SIZE       2.0
set TARGET_ASPECT   5.0
set MIN_JACOBIAN    0.50
set MAX_WARPAGE     20.0
set MAX_SKEW        60.0
set QUAD_MODE       "mixed"
set FEATURE_ANGLE   30.0
set MIN_LEN_FACTOR  0.30
set MAX_LEN_FACTOR  1.80
set OUT_DIR         "C:/work/mesh_hex"
set BASE_NAME       "YL_H2"

if {[info exists ::YL_ELEM_SIZE] && $::YL_ELEM_SIZE > 0.05} { set ELEM_SIZE [expr {double($::YL_ELEM_SIZE)}] }
if {[info exists ::YL_ASPECT]}       { set TARGET_ASPECT [expr {double($::YL_ASPECT)}] }
if {[info exists ::YL_MIN_JACOBIAN]} { set MIN_JACOBIAN  [expr {double($::YL_MIN_JACOBIAN)}] }
if {[info exists ::YL_MAX_WARPAGE]}  { set MAX_WARPAGE   [expr {double($::YL_MAX_WARPAGE)}] }
if {[info exists ::YL_MAX_SKEW]}     { set MAX_SKEW      [expr {double($::YL_MAX_SKEW)}] }
if {[info exists ::YL_QUAD_MODE]}    { set QUAD_MODE     [string tolower [string trim $::YL_QUAD_MODE]] }
if {[info exists ::YL_OUT_DIR]}      { set OUT_DIR       $::YL_OUT_DIR }
if {[info exists ::YL_BASE_NAME]}    { set BASE_NAME     $::YL_BASE_NAME }
file mkdir $OUT_DIR

proc ylh2_count {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}

proc ylh2_write_quad_bm_files {dir stem s feature_angle min_f max_f quad_mode min_jac max_warp max_skew max_asp} {
    set cr [file join $dir "${stem}.criteria"]
    set pr [file join $dir "${stem}.param"]
    set etype_code [expr {$quad_mode eq "quads" ? 1 : 2}]  ;# 1 = quads only, 2 = mixed (quad-dominant)

    set f [open $cr w]
    puts $f "This Criteria file was created with Criteria Editor application\n"
    puts $f " #    Criterion     On  Wt   Ideal    Good    Warn    Fail   Worst  Solver \n"
    puts $f " 0 penalty value               0.00    0.00    1.00    2.00   10.00         "
    puts $f [format " 1 min length         1 2.0 %7.3f %7.3f %7.3f %7.3f %7.3f       2 " $s [expr {0.6*$s}] [expr {0.45*$s}] [expr {$min_f*$s}] [expr {0.8*$min_f*$s}]]
    puts $f [format " 2 max length         1 1.5 %7.3f %7.3f %7.3f %7.3f %7.3f         " $s [expr {1.24*$s}] [expr {1.46*$s}] [expr {$max_f*$s}] [expr {1.25*$max_f*$s}]]
    puts $f [format " 3 aspect ratio       1 1.5    1.00    2.00    3.00 %7.2f %7.2f       0 " $max_asp [expr {2.0*$max_asp}]]
    puts $f [format " 4 warpage            1 2.5    0.00    5.00   12.00 %7.2f %7.2f       0 " $max_warp [expr {2.0*$max_warp}]]
    puts $f " 5 max angle quad     1 1.5   90.00  110.00  125.00  135.00  150.00       0 "
    puts $f " 6 min angle quad     1 1.5   90.00   70.00   55.00   45.00   30.00       0 "
    puts $f " 7 max angle tria     1 1.0   60.00   80.00  110.00  120.00  145.00       0 "
    puts $f " 8 min angle tria     1 1.0   60.00   50.00   35.00   25.00   15.00       0 "
    puts $f [format " 9 skew               1 1.5    0.00   15.00   40.00 %7.2f   75.00       0 " $max_skew]
    puts $f [format "10 jacobian           1 3.0    1.00    0.90    0.70 %7.2f   0.35       0 " $min_jac]
    puts $f "11 chordal dev        0 0.0    0.00    0.30    0.80    1.00    2.00         "
    puts $f "12 % of trias         1 2.0    0.00    2.00    4.00    5.00   10.00         "
    puts $f "13 taper              1 1.0    0.00    0.20    0.45    0.55    0.80       0 "
    close $f

    set k [expr {$s / 5.0}]
    set f [open $pr w]
    puts $f "#This Param file was created with Criteria/Param Editor application
geometry_cleanup_flag             1
meshing_flag                      1
element_size                      $s
element_type                      $etype_code
mesh_align                        3
element_order                     1
surf_component                    1
feature_angle                     $feature_angle
holes_table_begin
appl_surf(0)
appl_solid(0)
appl_cordsfiles(0)
appl_flanged_suppr(0)
flanged_suppr_height(2.0)
narrow_slots_type(0)
abs_fixed_nodes_count_max(-8)
holes_table_end
edge_fillet_recognition           0
max_fillet_radius                 [format %.4g [expr {12*$k}]]
surface_fillet_table_begin
surface_fillet_recognition(1)
minimize_transitions(1)
[format "rad(0,%.4g) wid(%.4g,%.4g) do elems(2) chordal_deviation(%.4g)" [expr {10*$k}] [expr {4.5*$k}] [expr {9*$k}]  [expr {1.5*$k}]]
[format "rad(0,%.4g) wid(%.4g,%.4g) do elems(2) chordal_deviation(%.4g)" [expr {30*$k}] [expr {9*$k}]   [expr {20*$k}] [expr {1.2*$k}]]
[format "rad(0,%.4g) wid(%.4g,%.4g) do elems(3) chordal_deviation(%.4g)" [expr {40*$k}] [expr {20*$k}]  [expr {30*$k}] [expr {0.8*$k}]]
surface_fillet_table_end
del_dupl_surfs_flag               2
del_dupl_surfs_tol                -1
edges_stitch_flags                0
max_edges_stitch_tol              -1.0
fix_overlapsurfs_flag             1
overlapsurfs_maxtangangle         -1.0
narrow_surfs_merge_width          -1.0
narrow_surfs_treat_flags          3
appl_tria_reduction               1
tria_redu_max_elemsize            auto
tria_redu_min_elemsize            auto
trias_around_holes                0
common_mesh_flow                  1
folded_elems_angle                150.0
smooth_elems_target               0.2
fillets_mesh_flow                 1
failed_elems_cleanup_flgs         8
move_nodes_across_feature_edges   1
featureedge_nodes_moveacross_max  [format %.4g [expr {0.05*$s}]]
move_nodes_across_free_edges      0
freeedge_nodes_moveacross_max     0.0
move_normal_flag                  0
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

*clearmark solids 1; *createmark solids 1 "all"
set n_solids [llength [hm_getmark solids 1]]
*clearmark solids 1

*clearmark surfs 1; *createmark surfs 1 "all"
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1
set n_surfs [llength $surf_ids]

if {$n_surfs == 0} {
    set ::YL_RESULT [list status "FAIL" engine "H2" mesher "BatchMesherQuad" msg "No surfaces found."]
    return
}

*clearmark elems 1; *createmark elems 1 "all"
if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
*clearmark elems 1
catch { *elementorder 1 }

set work_dir [expr {[info exists ::YL_LOG_DIR] && $::YL_LOG_DIR ne "" ? $::YL_LOG_DIR : $OUT_DIR}]
set tmp_stem "_ylh2_[pid]"
lassign [ylh2_write_quad_bm_files $work_dir $tmp_stem $ELEM_SIZE $FEATURE_ANGLE $MIN_LEN_FACTOR $MAX_LEN_FACTOR $QUAD_MODE $MIN_JACOBIAN $MAX_WARPAGE $MAX_SKEW $TARGET_ASPECT] bm_cr bm_pr

*clearmark lines 1; *createmark lines 1 "all"
catch { *maskmark lines 1 }
*clearmark lines 1

set bm_err ""
*clearmark surfs 1
eval *createmark surfs 1 $surf_ids
if {[catch { *hm_batchmesh 1 $bm_cr $bm_pr } err]} { set bm_err "hm_batchmesh(quad): $err" }
*clearmark surfs 1
catch { *unmaskall2 }
catch { file delete $bm_cr $bm_pr }
foreach f [glob -nocomplain -directory $work_dir "*${tmp_stem}.param.hm"] { catch { file delete $f } }

*clearmark elems 1
*createmark elems 1 "by config" quad4 tria3
if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 [expr {0.001 * $ELEM_SIZE}] 1 0 0 } }
*clearmark elems 1

set n_quad [ylh2_count quad4]
set n_tria [ylh2_count tria3]
set bad_jac -1; set bad_warp -1; set bad_asp -1; set bad_skew -1
if {$n_quad + $n_tria > 0} {
    foreach {var cmd lim lbl} [list \
        bad_jac  *elementtestjacobian $MIN_JACOBIAN  "2D Jacobian" \
        bad_warp *elementtestwarpage  $MAX_WARPAGE   "2D Warpage" \
        bad_asp  *elementtestaspect   $TARGET_ASPECT "2D Aspect" \
        bad_skew *elementtestskew     $MAX_SKEW      "2D Skew"] {
        *clearmark elems 1; *clearmark elems 2
        *createmark elems 1 "by config" quad4 tria3
        if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} { set $var [llength [hm_getmark elems 2]] }
        *clearmark elems 1; *clearmark elems 2
    }
}

set out_stem "${BASE_NAME}_2DQuad_${ELEM_SIZE}mm"
if {[info exists ::YL_OUT_NAME] && [string trim $::YL_OUT_NAME] ne ""} { set out_stem "[string trim $::YL_OUT_NAME]_2DQuad" }
set out_hm "$OUT_DIR/${out_stem}.hm"
catch { hm_answernext yes }
set write_failed [catch { *writefile "$out_hm" 1 } write_err]

set pass_2d [expr {$bm_err eq "" && $n_quad > 0 && $bad_jac == 0 && $bad_warp == 0 && $bad_asp == 0 ? 1 : 0}]
set _status [expr {$pass_2d && !$write_failed ? "OK" : "FAIL"}]
set msg [format "YL_H2 (2D Quad/Mixed 스킨) 완료\n- Size: %.2f mm, Mode: %s\n- Elements: Quad4=%d, Tria3=%d\n- Quality: BadJac=%d, BadWarp=%d, BadAsp=%d, BadSkew=%d\n- Saved: %s" \
    $ELEM_SIZE $QUAD_MODE $n_quad $n_tria $bad_jac $bad_warp $bad_asp $bad_skew $out_hm]
if {$bm_err ne ""} { append msg "\n*** $bm_err" }

set ::YL_RESULT [list status $_status engine "H2" mesher "BatchMesherQuad" size $ELEM_SIZE etype "2D_QUAD" \
    solids $n_solids unmap_solids $n_solids surfs $n_surfs n_hex 0 n_penta 0 n_quad $n_quad n_tria $n_tria \
    bad_jac $bad_jac bad_warp $bad_warp bad_asp $bad_asp bad_skew $bad_skew neg_jac 0 \
    jac_min "" jac_mean "" warp_max "" asp_p95 "" smoothed_nodes 0 \
    pass_hex $pass_2d fallback_h2 0 out $out_hm inp "" msg $msg]
'@

$rc = @(Start-YL) | Select-Object -Last 1
exit ([int]$rc)