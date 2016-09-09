#battery module for py3status

Uses acpi output to display battery info.

Displays:

* battery life
* plugged in or not status
* time remaining

Future:

- change colour when threshold (customizable)
- smoothing function

---

### dump 

from i3status
battery 0 {
    format = "%status %percentage %remaining"
	low_threshold = 30
	threshold_type = time
}

from i3status man-page
           battery 0 {
                   format = "%status %percentage %remaining %emptytime"
                   format_down = "No battery"
                   status_chr = "⚇ CHR"
                   status_bat = "⚡ BAT"
                   status_full = "☻ FULL"
                   path = "/sys/class/power_supply/BAT%d/uevent"
                   low_threshold = 10
           }

###smoothing:

x_new = 1/k * (current_measurement) + (1 - 1/k) * x_old

crank up k to have it smooth but unresponsive
make k small it have it fast but jittery
you just need to store x_old


'''
###raw data sample
 .:[18:26:45]:. ~ % acpi -bi
Battery 0: Charging, 87%, 00:17:52 until charged
Battery 0: design capacity 4239 mAh, last full capacity 4060 mAh = 95%

'''


##user config options


Sources:

https://github.com/ltworf/python-acpi/blob/master/acpi.py
https://i3wm.org/i3status/manpage.html#_battery


