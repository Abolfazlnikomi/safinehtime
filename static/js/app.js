// اثرنگار - سیستم حضور و غیاب
// Main JavaScript Application

const $ = window.jQuery
const AOS = window.AOS
const Chart = window.Chart

$(document).ready(() => {
  // Initialize AOS Animation
  if (typeof AOS !== "undefined") {
    AOS.init({
      duration: 800,
      easing: "ease-in-out",
      once: true,
      offset: 100,
    })
  }

  // Initialize tooltips
  initializeTooltips()

  // Initialize modals
  initializeModals()

  // Initialize form validation
  initializeFormValidation()

  // Initialize data tables
  initializeDataTables()

  // Initialize charts
  initializeCharts()

  // Initialize Persian date picker
  initializePersianDatePicker()

  // Initialize real-time updates
  initializeRealTimeUpdates()
})

// Tooltip System
function initializeTooltips() {
  $("[data-tooltip]").hover(
    function () {
      const text = $(this).data("tooltip")
      const tooltip = $(`<div class="tooltip show">${text}</div>`)
      $("body").append(tooltip)

      const rect = this.getBoundingClientRect()
      tooltip.css({
        top: rect.top - tooltip.outerHeight() - 5,
        left: rect.left + rect.width / 2 - tooltip.outerWidth() / 2,
      })
    },
    () => {
      $(".tooltip").remove()
    },
  )
}

// Modal System
function initializeModals() {
  // Close modal on overlay click
  $(document).on("click", ".modal-overlay", function (e) {
    if (e.target === this) {
      $(this).addClass("hidden")
    }
  })

  // Close modal on escape key
  $(document).keydown((e) => {
    if (e.keyCode === 27) {
      $(".modal-overlay:not(.hidden)").addClass("hidden")
    }
  })
}

// Form Validation
function initializeFormValidation() {
  $("form[data-validate]").submit(function (e) {
    const form = $(this)
    let isValid = true

    // Clear previous errors
    form.find(".error-message").remove()
    form.find(".border-red-500").removeClass("border-red-500")

    // Validate required fields
    form.find("[required]").each(function () {
      const field = $(this)
      const value = field.val().trim()

      if (!value) {
        showFieldError(field, "این فیلد الزامی است")
        isValid = false
      }
    })

    // Validate email fields
    form.find('input[type="email"]').each(function () {
      const field = $(this)
      const value = field.val().trim()

      if (value && !isValidEmail(value)) {
        showFieldError(field, "آدرس ایمیل معتبر نیست")
        isValid = false
      }
    })

    // Validate phone fields
    form.find('input[data-type="phone"]').each(function () {
      const field = $(this)
      const value = field.val().trim()

      if (value && !isValidPhone(value)) {
        showFieldError(field, "شماره تلفن معتبر نیست")
        isValid = false
      }
    })

    // Validate national ID
    form.find('input[data-type="national-id"]').each(function () {
      const field = $(this)
      const value = field.val().trim()

      if (value && !isValidNationalId(value)) {
        showFieldError(field, "کد ملی معتبر نیست")
        isValid = false
      }
    })

    if (!isValid) {
      e.preventDefault()
      showToast("لطفاً خطاهای فرم را برطرف کنید", "error")
    }
  })
}

// Show field error
function showFieldError(field, message) {
  field.addClass("border-red-500")
  field.after(`<div class="error-message text-red-500 text-sm mt-1">${message}</div>`)
}

// Validation helpers
function isValidEmail(email) {
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  return regex.test(email)
}

function isValidPhone(phone) {
  const regex = /^(\+98|0)?9\d{9}$/
  return regex.test(phone.replace(/\s/g, ""))
}

function isValidNationalId(nationalId) {
  if (nationalId.length !== 10) return false

  const check = Number.parseInt(nationalId[9])
  let sum = 0

  for (let i = 0; i < 9; i++) {
    sum += Number.parseInt(nationalId[i]) * (10 - i)
  }

  const remainder = sum % 11
  return (remainder < 2 && check === remainder) || (remainder >= 2 && check === 11 - remainder)
}

// Data Tables
function initializeDataTables() {
  $(".data-table").each(function () {
    const table = $(this)

    // Add search functionality
    const searchInput = table.closest(".card").find("input[data-search]")
    if (searchInput.length) {
      searchInput.on("input", function () {
        const searchTerm = $(this).val().toLowerCase()
        table.find("tbody tr").each(function () {
          const row = $(this)
          const text = row.text().toLowerCase()
          row.toggle(text.includes(searchTerm))
        })
      })
    }

    // Add sorting functionality
    table.find("th[data-sort]").click(function () {
      const column = $(this).data("sort")
      const order = $(this).hasClass("sort-asc") ? "desc" : "asc"

      // Remove existing sort classes
      table.find("th").removeClass("sort-asc sort-desc")
      $(this).addClass(`sort-${order}`)

      sortTable(table, column, order)
    })
  })
}

// Sort table
function sortTable(table, column, order) {
  const rows = table.find("tbody tr").toArray()
  const columnIndex = table.find(`th[data-sort="${column}"]`).index()

  rows.sort((a, b) => {
    const aValue = $(a).find("td").eq(columnIndex).text().trim()
    const bValue = $(b).find("td").eq(columnIndex).text().trim()

    if (order === "asc") {
      return aValue.localeCompare(bValue, "fa")
    } else {
      return bValue.localeCompare(aValue, "fa")
    }
  })

  table.find("tbody").empty().append(rows)
}

// Charts
function initializeCharts() {
  // Attendance Chart
  const attendanceCtx = document.getElementById("attendanceChart")
  if (attendanceCtx && typeof Chart !== "undefined") {
    new Chart(attendanceCtx, {
      type: "line",
      data: {
        labels: ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"],
        datasets: [
          {
            label: "حضور (ساعت)",
            data: [8, 7.5, 8, 8.5, 7, 8, 0],
            borderColor: "rgb(34, 197, 94)",
            backgroundColor: "rgba(34, 197, 94, 0.1)",
            tension: 0.4,
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: false,
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            max: 10,
            title: {
              display: true,
              text: "ساعت",
            },
          },
        },
      },
    })
  }

  // Status Pie Chart
  const statusCtx = document.getElementById("statusChart")
  if (statusCtx && typeof Chart !== "undefined") {
    new Chart(statusCtx, {
      type: "doughnut",
      data: {
        labels: ["حاضر", "غایب", "تأخیر", "مرخصی"],
        datasets: [
          {
            data: [75, 10, 10, 5],
            backgroundColor: ["rgb(34, 197, 94)", "rgb(239, 68, 68)", "rgb(245, 158, 11)", "rgb(59, 130, 246)"],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
          },
        },
      },
    })
  }
}

// Persian Date Picker
function initializePersianDatePicker() {
  $("input[data-persian-date]").each(function () {
    const input = $(this)

    // Create date picker container
    const picker = $(`
            <div class="persian-calendar hidden absolute z-10 mt-1 bg-white border border-gray-300 rounded-lg shadow-lg">
                <div class="persian-calendar-header">
                    <button type="button" class="prev-month">&lt;</button>
                    <span class="current-month"></span>
                    <button type="button" class="next-month">&gt;</button>
                </div>
                <div class="persian-calendar-grid">
                    <!-- Calendar days will be generated here -->
                </div>
            </div>
        `)

    input.after(picker)

    // Show/hide picker
    input.click(() => {
      $(".persian-calendar").addClass("hidden")
      picker.removeClass("hidden")
      generateCalendar(picker, new Date())
    })

    // Hide picker when clicking outside
    $(document).click((e) => {
      if (!input.is(e.target) && !picker.is(e.target) && picker.has(e.target).length === 0) {
        picker.addClass("hidden")
      }
    })
  })
}

// Generate calendar
function generateCalendar(picker, date) {
  const grid = picker.find(".persian-calendar-grid")
  grid.empty()

  // Add day headers
  const dayHeaders = ["ش", "ی", "د", "س", "چ", "پ", "ج"]
  dayHeaders.forEach((day) => {
    grid.append(`<div class="text-center text-sm font-medium text-gray-500 p-2">${day}</div>`)
  })

  // Add calendar days (simplified)
  for (let i = 1; i <= 30; i++) {
    const dayElement = $(`<div class="persian-calendar-day">${i}</div>`)

    dayElement.click(() => {
      const selectedDate = `1403/08/${i.toString().padStart(2, "0")}`
      picker.prev("input").val(selectedDate)
      picker.addClass("hidden")
    })

    grid.append(dayElement)
  }
}

// Real-time Updates
function initializeRealTimeUpdates() {
  // Update time every second
  setInterval(updateCurrentTime, 1000)

  // Update attendance status every 5 minutes
  setInterval(updateAttendanceStatus, 300000)
}

// Update current time
function updateCurrentTime() {
  const now = new Date()
  const timeString = now.toLocaleTimeString("fa-IR")
  $(".current-time").text(timeString)
}


// Update attendance status
function updateAttendanceStatus() {
  console.log("Updating attendance status...")
}

// Toast Notification System
function showToast(message, type) {
  type = type || "success"
  const toast = $(`
        <div class="notification-toast ${type}">
            <div class="flex items-center">
                <i class="fas ${getToastIcon(type)} ml-2"></i>
                <span>${message}</span>
                <button class="mr-4 hover:text-gray-200" onclick="$(this).closest('.notification-toast').remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        </div>
    `)

  $("body").append(toast)

  // Show toast
  setTimeout(() => {
    toast.addClass("show")
  }, 100)

  // Auto hide after 5 seconds
  setTimeout(() => {
    toast.removeClass("show")
    setTimeout(() => {
      toast.remove()
    }, 300)
  }, 5000)
}

// Get toast icon based on type
function getToastIcon(type) {
  const icons = {
    success: "fa-check-circle",
    error: "fa-exclamation-circle",
    warning: "fa-exclamation-triangle",
    info: "fa-info-circle",
  }
  return icons[type] || icons.info
}

// Fingerprint Scanner Simulation
function simulateFingerprintScan(callback) {
  const scanner = $(".fingerprint-scanner")
  scanner.addClass("scanning")

  // Simulate scanning process
  setTimeout(() => {
    scanner.removeClass("scanning")

    // Simulate success/failure
    const success = Math.random() > 0.2 // 80% success rate

    if (success) {
      showToast("اثر انگشت با موفقیت تشخیص داده شد", "success")
      if (callback) callback(true)
    } else {
      showToast("اثر انگشت تشخیص داده نشد. لطفاً دوباره تلاش کنید.", "error")
      if (callback) callback(false)
    }
  }, 3000)
}

// Export functions for global use
window.showToast = showToast
window.simulateFingerprintScan = simulateFingerprintScan
window.showFieldError = showFieldError
