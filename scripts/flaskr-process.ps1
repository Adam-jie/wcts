function Test-FlaskrProcessOwnership {
    param([string]$CommandLine, [string]$WorkerPath, [string]$ProjectPath, [string]$DatabasePath, [int]$Port)
    # Match the complete argument tail emitted by start-flaskr, including both
    # data and source identities. A shared worker executable alone is insufficient.
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $expectedTail = '"' + $WorkerPath + '" --project "' + $ProjectPath + '" --database "' + $DatabasePath + '" --port ' + $Port + ' --seed-demo'
    return $CommandLine.TrimEnd().EndsWith($expectedTail, [System.StringComparison]::OrdinalIgnoreCase)
}
