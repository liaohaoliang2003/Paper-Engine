param(
  [string]$paper_source_path,
  [string]$md_path,
  [string]$html_path,
  [string]$json_out
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Throw-InputError {
  param([string]$Message)
  $ex = New-Object System.Exception($Message)
  $ex.Data['ExitCode'] = 2
  throw $ex
}

function New-Check {
  param(
    [string]$Id,
    [string]$Level,
    [string]$Name,
    [bool]$Passed,
    [string]$Detail
  )

  [pscustomobject]@{
    id = $Id
    level = $Level
    name = $Name
    passed = $Passed
    detail = $Detail
  }
}

try {
  $hasPaper = -not [string]::IsNullOrWhiteSpace($paper_source_path)
  if (-not $hasPaper -and [string]::IsNullOrWhiteSpace($md_path) -and [string]::IsNullOrWhiteSpace($html_path)) {
    Throw-InputError '必须提供 paper_source_path 或 md_path/html_path。'
  }

  $paperResolved = $null
  $paperDir = $null
  $paperStem = $null
  $pdfFile = $null

  if ($hasPaper) {
    if (-not (Test-Path -LiteralPath $paper_source_path)) {
      Throw-InputError "paper_source_path 不存在：$paper_source_path"
    }
    $paperResolved = (Resolve-Path -LiteralPath $paper_source_path).Path
    $paperDir = Split-Path -Parent $paperResolved
    $paperStem = [System.IO.Path]::GetFileNameWithoutExtension($paperResolved)
    $pdfFile = [System.IO.Path]::GetFileName($paperResolved)
  }

  $resolvedMd = if (-not [string]::IsNullOrWhiteSpace($md_path)) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($md_path)
  } elseif ($hasPaper) {
    Join-Path $paperDir ("{0}_report.md" -f $paperStem)
  } else {
    Throw-InputError '未提供 md_path，且无法从 paper_source_path 推导。'
  }

  $resolvedHtml = if (-not [string]::IsNullOrWhiteSpace($html_path)) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($html_path)
  } elseif ($hasPaper) {
    Join-Path $paperDir ("{0}_report.html" -f $paperStem)
  } else {
    Throw-InputError '未提供 html_path，且无法从 paper_source_path 推导。'
  }

  $resolvedJson = if (-not [string]::IsNullOrWhiteSpace($json_out)) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($json_out)
  } elseif ($hasPaper) {
    Join-Path $paperDir ("{0}_validation.json" -f $paperStem)
  } else {
    Join-Path (Split-Path -Parent $resolvedMd) (([System.IO.Path]::GetFileNameWithoutExtension($resolvedMd)) + '_validation.json')
  }

  $checks = New-Object System.Collections.Generic.List[object]

  $mdExists = Test-Path -LiteralPath $resolvedMd
  $checks.Add((New-Check -Id 'H001' -Level 'hard' -Name 'Markdown 文件存在' -Passed $mdExists -Detail $resolvedMd))

  $htmlExists = Test-Path -LiteralPath $resolvedHtml
  $checks.Add((New-Check -Id 'H002' -Level 'hard' -Name 'HTML 文件存在' -Passed $htmlExists -Detail $resolvedHtml))

  if (-not $mdExists -or -not $htmlExists) {
    throw (New-Object System.Exception('关键输入文件缺失，无法继续校验。'))
  }

  $md = Get-Content -LiteralPath $resolvedMd -Raw -Encoding UTF8
  $html = Get-Content -LiteralPath $resolvedHtml -Raw -Encoding UTF8

  $expectedSections = @(
    '1、简短总结',
    '2、核心贡献与新颖性',
    '3、针对的问题',
    '4、分段详解',
    '5、局限性与风险评估',
    '6、后续可能的创新点和改进点',
    '7、复现计划'
  )

  $h1Matches = [regex]::Matches($md, '(?m)^#\s+(.+)$')
  $actualSections = @()
  foreach ($m in $h1Matches) { $actualSections += $m.Groups[1].Value.Trim() }

  $sectionOrderPassed = $false
  if ($actualSections.Count -ge 7) {
    $sectionOrderPassed = ($actualSections[0..6] -join '||') -eq ($expectedSections -join '||')
  }
  $actualPreview = if ($actualSections.Count -gt 0) {
    $actualSections[0..([Math]::Min(6, $actualSections.Count - 1))] -join ' | '
  } else {
    ''
  }
  $checks.Add((New-Check -Id 'H101' -Level 'hard' -Name '前7个一级章节顺序固定' -Passed $sectionOrderPassed -Detail ('actual=' + $actualPreview)))

  $sec4Match = [regex]::Match($md, '(?ms)^#\s+4、分段详解\s*(.*?)(?=^#\s+\d+、|\z)')
  $sec4Body = if ($sec4Match.Success) { $sec4Match.Groups[1].Value } else { '' }
  $level2Count = [regex]::Matches($sec4Body, '(?m)^##\s+').Count
  $checks.Add((New-Check -Id 'H102' -Level 'hard' -Name '第4章含二级标题结构' -Passed ($level2Count -ge 1) -Detail ("level2_count=$level2Count")))

  $slotKeywords = @('小节总结', '深入分析', '术语解释：', '示例或类比')
  $slotMissing = @()
  foreach ($kw in $slotKeywords) {
    if ($sec4Body -notmatch [regex]::Escape($kw)) { $slotMissing += $kw }
  }
  $slotDetail = if ($slotMissing.Count -eq 0) { 'ok' } else { 'missing=' + ($slotMissing -join ',') }
  $checks.Add((New-Check -Id 'H103' -Level 'hard' -Name '第4章含四槽位要素' -Passed ($slotMissing.Count -eq 0) -Detail $slotDetail))

  $anchorCount = [regex]::Matches($md, '(?m)^证据锚点：.+$').Count
  $checks.Add((New-Check -Id 'H104' -Level 'hard' -Name '证据锚点行存在' -Passed ($anchorCount -ge 1) -Detail ("anchor_count=$anchorCount")))

  $termLineCount = [regex]::Matches($md, '(?m)^术语解释：.+$').Count
  $checks.Add((New-Check -Id 'H105' -Level 'hard' -Name '术语解释行存在' -Passed ($termLineCount -ge 1) -Detail ("term_line_count=$termLineCount")))

  $charCount = $md.Length
  $checks.Add((New-Check -Id 'H106' -Level 'hard' -Name '报告最小篇幅>=4500字符' -Passed ($charCount -ge 4500) -Detail ("char_count=$charCount")))

  $termLines = @(($md -split "`r?`n") | Where-Object { $_ -match '^术语解释：' })
  $termLinesWithEnglish = @($termLines | Where-Object { $_ -match '[A-Za-z]' })
  $invalidBilingual = @($termLinesWithEnglish | Where-Object { $_ -notmatch '[A-Za-z][A-Za-z0-9\-\+\./\s]*（[^）]+）' })
  $bilingualPass = ($termLinesWithEnglish.Count -eq 0) -or ($invalidBilingual.Count -eq 0)
  $checks.Add((New-Check -Id 'H107' -Level 'hard' -Name '术语双语格式可检测通过' -Passed $bilingualPass -Detail ("english_lines=$($termLinesWithEnglish.Count),invalid=$($invalidBilingual.Count)")))

  $pdfLinkPass = if ($hasPaper) {
    $html -match [regex]::Escape("./$pdfFile")
  } else {
    $html -match '<a\s+href="\./[^"<>]+\.pdf"'
  }
  $pdfLinkDetail = if ($hasPaper) { "expect=./$pdfFile" } else { 'expect=./*.pdf' }
  $checks.Add((New-Check -Id 'H201' -Level 'hard' -Name 'HTML包含原论文链接' -Passed $pdfLinkPass -Detail $pdfLinkDetail))

  $accordionPass = ($html -match 'accordion-trigger') -and ($html -match 'accordion-panel')
  $checks.Add((New-Check -Id 'H202' -Level 'hard' -Name 'HTML包含手风琴组件' -Passed $accordionPass -Detail 'need accordion-trigger + accordion-panel'))

  $a11yPass = ($html -match 'aria-expanded') -and ($html -match "e.key === 'Enter'") -and ($html -match "e.key === ' '")
  $checks.Add((New-Check -Id 'H203' -Level 'hard' -Name 'HTML包含可访问性交互' -Passed $a11yPass -Detail 'need aria-expanded + Enter/Space'))

  $emptyPanel = [regex]::IsMatch($html, '<div class="accordion-panel"[^>]*>\s*<p>\s*</p>', 'Singleline')
  $checks.Add((New-Check -Id 'H204' -Level 'hard' -Name 'HTML手风琴无空面板' -Passed (-not $emptyPanel) -Detail ("empty_panel=$emptyPanel")))

  $extSectionCount = [Math]::Max(0, $actualSections.Count - 7)
  $checks.Add((New-Check -Id 'S301' -Level 'soft' -Name '扩展章节位于第7章后' -Passed $true -Detail ("extra_h1_count=$extSectionCount")))

  $hardFailed = @($checks | Where-Object { $_.level -eq 'hard' -and -not $_.passed })
  $softFailed = @($checks | Where-Object { $_.level -eq 'soft' -and -not $_.passed })

  $result = [pscustomobject]@{
    timestamp = (Get-Date).ToString('s')
    input = [pscustomobject]@{
      paper_source_path = $paperResolved
      md_path = $resolvedMd
      html_path = $resolvedHtml
      json_out = $resolvedJson
    }
    summary = [pscustomobject]@{
      total = $checks.Count
      hard_failed = $hardFailed.Count
      soft_failed = $softFailed.Count
      passed = ($hardFailed.Count -eq 0)
      failure_ids = @($hardFailed | ForEach-Object { $_.id })
    }
    checks = $checks
  }

  $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resolvedJson -Encoding UTF8

  Write-Host ("Validation summary: total={0}, hard_failed={1}, soft_failed={2}" -f $checks.Count, $hardFailed.Count, $softFailed.Count)
  if ($hardFailed.Count -gt 0) {
    Write-Host ("Hard failure IDs: {0}" -f (($hardFailed | ForEach-Object { $_.id }) -join ', '))
    exit 4
  }

  Write-Host ("Validation pass. JSON: {0}" -f $resolvedJson)
  exit 0
}
catch {
  $exitCode = 4
  if ($_.Exception -and $_.Exception.Data.Contains('ExitCode')) {
    $exitCode = [int]$_.Exception.Data['ExitCode']
  }
  [Console]::Error.WriteLine($_.Exception.Message)
  exit $exitCode
}
