Param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

python "$PSScriptRoot/nus_logger.py" @Args
