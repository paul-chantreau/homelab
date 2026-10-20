# Read-only, console-only equivalent of scripts/discover-network.sh.
[CmdletBinding()]
param(
    [string]$InterfaceAlias
)

$ErrorActionPreference = "Stop"

function Get-UsableIPv4 {
    param([object[]]$Values)
    foreach ($value in $Values) {
        $address = $null
        if ([System.Net.IPAddress]::TryParse([string]$value, [ref]$address) -and
            $address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
            -not [System.Net.IPAddress]::IsLoopback($address)) {
            $firstByte = $address.GetAddressBytes()[0]
            if ($address.ToString() -ne "0.0.0.0" -and $firstByte -lt 224) {
                $address.ToString()
            }
        }
    }
}

$adapters = @(Get-NetAdapter)
$dnsEntries = @(Get-DnsClientServerAddress -AddressFamily IPv4)
$interfaces = @(foreach ($configuration in @(Get-NetIPConfiguration)) {
    $adapter = $adapters | Where-Object { $_.ifIndex -eq $configuration.InterfaceIndex } | Select-Object -First 1
    $servers = @($dnsEntries | Where-Object { $_.InterfaceIndex -eq $configuration.InterfaceIndex } |
        ForEach-Object { $_.ServerAddresses })
    [pscustomobject]@{
        Name = $configuration.InterfaceAlias
        State = [string]$adapter.Status
        Mac = ([string]$adapter.MacAddress).Replace("-", ":").ToLowerInvariant()
        Addresses = @($configuration.IPv4Address | Where-Object { $_.IPAddress } | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" })
        Prefixes = @($configuration.IPv4Address | Where-Object { $_.IPAddress } | ForEach-Object { $_.PrefixLength } | Select-Object -Unique)
        Gateways = @(Get-UsableIPv4 @($configuration.IPv4DefaultGateway | ForEach-Object { $_.NextHop }) | Select-Object -Unique)
        Dns = @(Get-UsableIPv4 $servers | Select-Object -Unique)
    }
})

Write-Output "Network discovery (console only)"
Write-Output "This is local IP configuration only. It does not identify Netgear VLAN membership, PVIDs, or physical switch ports."
Write-Output ""
$interfaces | Select-Object Name, State, Mac, @{Name="IPv4"; Expression={ $_.Addresses -join ", " }} |
    Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output

$usable = @($interfaces | Where-Object { $_.State -eq "Up" -and $_.Addresses.Count -gt 0 })
if ($InterfaceAlias) {
    $candidateInterfaces = @($usable | Where-Object { $_.Name -eq $InterfaceAlias })
    if ($candidateInterfaces.Count -ne 1) {
        throw "Interface '$InterfaceAlias' is not up with an IPv4 address."
    }
} else {
    $candidateInterfaces = @($usable | Where-Object { $_.Gateways.Count -gt 0 })
}

$gateway = "<CONFIRM_ROUTER_IPV4>"
$dns = "<CONFIRM_IPV4_DNS_JSON_LIST>"
$prefix = "<PREFIX_LENGTH>"
if ($candidateInterfaces.Count -eq 1) {
    $selected = $candidateInterfaces[0]
    Write-Output "Local details (this computer, not the MAAS server identity):"
    Write-Output "LOCAL_INTERFACE=$($selected.Name)"
    Write-Output "LOCAL_ADDRESS=$($selected.Addresses -join ',')"
    Write-Output "LOCAL_MAC=$($selected.Mac)"
    if ($selected.Prefixes.Count -eq 1) { $prefix = [string]$selected.Prefixes[0] }
    if ($selected.Gateways.Count -eq 1) { $gateway = $selected.Gateways[0] }
    if ($selected.Dns.Count -gt 0) {
        $dns = ConvertTo-Json -InputObject @($selected.Dns) -Compress
    }
} else {
    Write-Output 'No single management interface selected. Rerun with -InterfaceAlias "Wi-Fi" (use your LAN adapter name).'
}

Write-Output ""
Write-Output "GitHub Actions Secrets (confirm detected gateway/DNS in the router UI):"
Write-Output "MANAGEMENT_INTERFACE_MAC=<MAAS_SERVER_ETHERNET_MAC>"
Write-Output "MANAGEMENT_ADDRESS=<UNUSED_MAAS_SERVER_IPV4>/$prefix"
Write-Output "MANAGEMENT_GATEWAY=$gateway"
Write-Output "MANAGEMENT_DNS=$dns"
Write-Output "MANAGEMENT_VLAN_ID=<MANAGEMENT_VLAN_FROM_SWITCH_UI>"
Write-Output ""
Write-Output "Replace <...> using the MAAS server identity, a chosen unused address, and the switch UI."
Write-Output "Do not use this computer's address or MAC as the MAAS identity unless it is that server."
Write-Output "The router UI provides the LAN subnet, DHCP scope, gateway, DNS, and switch DHCP lease."
Write-Output "The switch UI provides VLAN membership, management VLAN, and PVIDs."
Write-Output "These network inputs are Secrets, not Variables. Nothing is saved or uploaded."
