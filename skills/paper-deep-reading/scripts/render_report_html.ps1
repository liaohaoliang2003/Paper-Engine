param(
  [Parameter(Mandatory = $true)]
  [string]$paper_source_path,
  [string]$md_path,
  [string]$html_path,
  [string]$title
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Throw-InputError {
  param([string]$Message)
  $ex = New-Object System.Exception($Message)
  $ex.Data['ExitCode'] = 2
  throw $ex
}

function Convert-InlineMarkdown {
  param([string]$Text)

  $safe = [System.Net.WebUtility]::HtmlEncode($Text)
  $safe = [System.Text.RegularExpressions.Regex]::Replace($safe, '`([^`]+)`', '<code>$1</code>')
  $safe = [System.Text.RegularExpressions.Regex]::Replace($safe, '\[([^\]]+)\]\(([^)]+)\)', '<a href="$2">$1</a>')
  $safe = [System.Text.RegularExpressions.Regex]::Replace($safe, '\*\*([^*]+)\*\*', '<strong>$1</strong>')
  $safe = [System.Text.RegularExpressions.Regex]::Replace($safe, '\*([^*]+)\*', '<em>$1</em>')
  return $safe
}

function Convert-MarkdownFragment {
  param([string]$Markdown)

  $cmd = Get-Command ConvertFrom-Markdown -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    return (ConvertFrom-Markdown -InputObject $Markdown).Html
  }

  $lines = $Markdown -split "`r?`n"
  $sb = New-Object System.Text.StringBuilder
  $inUl = $false
  $inOl = $false
  $inCode = $false

  foreach ($lineRaw in $lines) {
    $line = $lineRaw.TrimEnd()

    if ($line -match '^```') {
      if (-not $inCode) {
        [void]$sb.AppendLine('<pre><code>')
        $inCode = $true
      } else {
        [void]$sb.AppendLine('</code></pre>')
        $inCode = $false
      }
      continue
    }

    if ($inCode) {
      [void]$sb.AppendLine([System.Net.WebUtility]::HtmlEncode($line))
      continue
    }

    if ([string]::IsNullOrWhiteSpace($line)) {
      if ($inUl) { [void]$sb.AppendLine('</ul>'); $inUl = $false }
      if ($inOl) { [void]$sb.AppendLine('</ol>'); $inOl = $false }
      continue
    }

    if ($line -match '^###\s+(.+)$') {
      if ($inUl) { [void]$sb.AppendLine('</ul>'); $inUl = $false }
      if ($inOl) { [void]$sb.AppendLine('</ol>'); $inOl = $false }
      [void]$sb.AppendLine("<h3>$(Convert-InlineMarkdown -Text $matches[1])</h3>")
      continue
    }
    if ($line -match '^##\s+(.+)$') {
      if ($inUl) { [void]$sb.AppendLine('</ul>'); $inUl = $false }
      if ($inOl) { [void]$sb.AppendLine('</ol>'); $inOl = $false }
      [void]$sb.AppendLine("<h2>$(Convert-InlineMarkdown -Text $matches[1])</h2>")
      continue
    }
    if ($line -match '^#\s+(.+)$') {
      if ($inUl) { [void]$sb.AppendLine('</ul>'); $inUl = $false }
      if ($inOl) { [void]$sb.AppendLine('</ol>'); $inOl = $false }
      [void]$sb.AppendLine("<h1>$(Convert-InlineMarkdown -Text $matches[1])</h1>")
      continue
    }
    if ($line -match '^\d+\.\s+(.+)$') {
      if ($inUl) { [void]$sb.AppendLine('</ul>'); $inUl = $false }
      if (-not $inOl) { [void]$sb.AppendLine('<ol>'); $inOl = $true }
      [void]$sb.AppendLine("<li>$(Convert-InlineMarkdown -Text $matches[1])</li>")
      continue
    }
    if ($line -match '^[-*]\s+(.+)$') {
      if ($inOl) { [void]$sb.AppendLine('</ol>'); $inOl = $false }
      if (-not $inUl) { [void]$sb.AppendLine('<ul>'); $inUl = $true }
      [void]$sb.AppendLine("<li>$(Convert-InlineMarkdown -Text $matches[1])</li>")
      continue
    }

    if ($inUl) { [void]$sb.AppendLine('</ul>'); $inUl = $false }
    if ($inOl) { [void]$sb.AppendLine('</ol>'); $inOl = $false }
    [void]$sb.AppendLine("<p>$(Convert-InlineMarkdown -Text $line)</p>")
  }

  if ($inUl) { [void]$sb.AppendLine('</ul>') }
  if ($inOl) { [void]$sb.AppendLine('</ol>') }
  if ($inCode) { [void]$sb.AppendLine('</code></pre>') }
  return $sb.ToString()
}

function Normalize-MarkdownLines {
  param([string]$Content)

  $lines = $Content -split "`r?`n"
  $out = New-Object System.Collections.Generic.List[string]
  $metaRegex = New-Object System.Text.RegularExpressions.Regex('^\s*(术语解释：|证据锚点：)')

  for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    $isMeta = $metaRegex.IsMatch($line)
    if ($isMeta) {
      $line = $line.TrimStart()
      if ($out.Count -gt 0 -and $out[$out.Count - 1].Trim() -ne '') {
        $out.Add('')
      }
      $out.Add($line)
      if ($i + 1 -lt $lines.Count -and $lines[$i + 1].Trim() -ne '') {
        $out.Add('')
      }
      continue
    }
    $out.Add($line)
  }

  $collapsed = New-Object System.Collections.Generic.List[string]
  $blank = 0
  foreach ($l in $out) {
    if ($l.Trim() -eq '') { $blank += 1 } else { $blank = 0 }
    if ($blank -le 2) { $collapsed.Add($l) }
  }

  return (($collapsed -join "`n").TrimEnd() + "`n")
}

function Build-AccordionHtml {
  param(
    [string]$Kind,
    [string]$BodyHtml
  )

  $label = if ($Kind -eq 'term') { '术语解释' } else { '证据锚点' }
  $itemClass = if ($Kind -eq 'term') { 'term-item' } else { 'evidence-item' }

  return @"
<div class="accordion-item $itemClass">
  <button class="accordion-trigger" aria-expanded="false" type="button">
    <span class="meta-label">$label</span><span class="caret">▸</span>
  </button>
  <div class="accordion-panel" hidden><p>$BodyHtml</p></div>
</div>
"@
}

function Render-ReportHtml {
  param(
    [string]$PaperPath,
    [string]$MdPath,
    [string]$HtmlPath,
    [string]$Title
  )

  $mdRaw = Get-Content -Path $MdPath -Raw -Encoding UTF8
  $mdNorm = Normalize-MarkdownLines -Content $mdRaw
  Set-Content -Path $MdPath -Value $mdNorm -Encoding UTF8

  $frag = Convert-MarkdownFragment -Markdown $mdNorm

  $termRegex = New-Object System.Text.RegularExpressions.Regex('<p>术语解释：(.*?)</p>', [System.Text.RegularExpressions.RegexOptions]::Singleline)
  $evidenceRegex = New-Object System.Text.RegularExpressions.Regex('<p>证据锚点：(.*?)</p>', [System.Text.RegularExpressions.RegexOptions]::Singleline)

  $frag = $termRegex.Replace($frag, [System.Text.RegularExpressions.MatchEvaluator]{
    param($m)
    $body = $m.Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($body)) { return $m.Value }
    return (Build-AccordionHtml -Kind 'term' -BodyHtml $body)
  })

  $frag = $evidenceRegex.Replace($frag, [System.Text.RegularExpressions.MatchEvaluator]{
    param($m)
    $body = $m.Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($body)) { return $m.Value }
    return (Build-AccordionHtml -Kind 'evidence' -BodyHtml $body)
  })

  $pdfFile = [System.IO.Path]::GetFileName($PaperPath)
  $paperAbs = [System.Net.WebUtility]::HtmlEncode((Resolve-Path -Path $PaperPath).Path)
  $sourceMeta = "<p class='source-meta'>原论文链接：<a href='./$pdfFile'>$pdfFile</a></p><p class='source-meta'>原文路径：$paperAbs</p>"

  $style = @"
<style>
:root{--line:#d9dee8;--text:#1f2328;--muted:#667085}
*{box-sizing:border-box}
body{margin:0;background:#f7f8fa;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:var(--text);line-height:1.78}
.page{max-width:1024px;margin:20px auto;padding:0 14px}
.article{background:#fff;border:1px solid #e7ebf2;border-radius:12px;padding:20px}
h1,h2,h3{line-height:1.35}
h1{border-bottom:1px solid #eef2f7;padding-bottom:8px}
p,li{font-size:16px}
code{background:#f3f5f8;padding:1px 5px;border-radius:5px}
a{color:#0969da;text-decoration:none}
a:hover{text-decoration:underline}
.source-meta{font-size:13px;color:#5d6678;margin:2px 0}

.toolbar{display:flex;gap:8px;justify-content:flex-end;margin:4px 0 10px}
.toolbar button{font-size:11px;color:#7a8394;background:transparent;border:0;cursor:pointer;padding:1px 3px}
.toolbar button:hover{color:#344054;text-decoration:underline}

.accordion-item{margin:3px 0}
.accordion-trigger{all:unset;display:inline-flex;align-items:center;gap:5px;cursor:pointer;color:#6f7787;font-size:11px;line-height:1.35;padding:0}
.accordion-trigger:hover{color:#344054}
.accordion-trigger:focus{outline:1px dashed #a3b2c7;outline-offset:2px}
.accordion-trigger .meta-label{font-weight:600}
.accordion-trigger .caret{font-size:10px;transition:transform .15s ease}
.accordion-trigger[aria-expanded='true'] .caret{transform:rotate(90deg)}
.accordion-panel{margin-top:2px;padding:4px 6px;border-left:1px solid #e7ecf3;background:transparent}
.accordion-panel p{margin:0;color:#3b475b;font-size:13px}

@media (max-width:720px){
  .article{padding:14px}
}
</style>
"@

  $script = @"
<script>
(function(){
  const items = Array.from(document.querySelectorAll('.accordion-item'));
  function setItem(item, expanded){
    const btn = item.querySelector('.accordion-trigger');
    const panel = item.querySelector('.accordion-panel');
    if(!btn || !panel) return;
    btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    panel.hidden = !expanded;
  }
  items.forEach(item => {
    const btn = item.querySelector('.accordion-trigger');
    if(!btn) return;
    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      setItem(item, !expanded);
    });
    btn.addEventListener('keydown', (e) => {
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        setItem(item, !expanded);
      }
    });
  });
  const expandAll = document.getElementById('expand-all');
  const collapseAll = document.getElementById('collapse-all');
  if(expandAll) expandAll.addEventListener('click', () => items.forEach(i => setItem(i, true)));
  if(collapseAll) collapseAll.addEventListener('click', () => items.forEach(i => setItem(i, false)));
})();
</script>
"@

  $controls = "<div class='toolbar'><button id='expand-all' type='button'>全部展开</button><button id='collapse-all' type='button'>全部收起</button></div>"

  $html = @"
<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>$Title</title>
$style
</head>
<body>
<main class='page'>
  <section class='article'>
    $sourceMeta
    $controls
    $frag
  </section>
</main>
$script
</body>
</html>
"@

  Set-Content -Path $HtmlPath -Value $html -Encoding UTF8
}

try {
  if (-not (Test-Path -LiteralPath $paper_source_path)) {
    Throw-InputError "paper_source_path 不存在：$paper_source_path"
  }

  $paperResolved = (Resolve-Path -LiteralPath $paper_source_path).Path
  $paperItem = Get-Item -LiteralPath $paperResolved
  if ($paperItem.PSIsContainer) {
    Throw-InputError "paper_source_path 必须是文件路径：$paperResolved"
  }

  $paperDir = Split-Path -Parent $paperResolved
  $paperStem = [System.IO.Path]::GetFileNameWithoutExtension($paperResolved)

  $resolvedMd = if ([string]::IsNullOrWhiteSpace($md_path)) {
    Join-Path $paperDir ("{0}_report.md" -f $paperStem)
  } else {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($md_path)
  }

  $resolvedHtml = if ([string]::IsNullOrWhiteSpace($html_path)) {
    Join-Path $paperDir ("{0}_report.html" -f $paperStem)
  } else {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($html_path)
  }

  if (-not (Test-Path -LiteralPath $resolvedMd)) {
    Throw-InputError "Markdown 报告不存在：$resolvedMd"
  }

  $resolvedTitle = if ([string]::IsNullOrWhiteSpace($title)) {
    "{0}_report" -f $paperStem
  } else {
    $title
  }

  Render-ReportHtml -PaperPath $paperResolved -MdPath $resolvedMd -HtmlPath $resolvedHtml -Title $resolvedTitle
  Write-Output ("render success: {0}" -f $resolvedHtml)
  exit 0
}
catch {
  $exitCode = 3
  if ($_.Exception -and $_.Exception.Data.Contains('ExitCode')) {
    $exitCode = [int]$_.Exception.Data['ExitCode']
  }
  [Console]::Error.WriteLine($_.Exception.Message)
  exit $exitCode
}
