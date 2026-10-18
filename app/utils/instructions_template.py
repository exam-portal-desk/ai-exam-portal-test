"""
app/utils/instructions_template.py
The General Exam Instructions template offered inside the Add / Edit Exam instructions editor ("Template" button). It is plain
text in exactly the syntax the editor and app/utils/instructions_formatter.py support (# / ## headings, "- " bullets, "1." steps
with two-space nesting, **bold**, IMPORTANT: callouts) — no other markers — so an admin can rewrite, reorder, bold or list any
part of it and it still renders the same on the student's Exam Instructions page.

Statements that hold for every exam are written as guidance and hedged with "according to the exam rules" wherever an exam
may differ. Everything that varies per exam (duration, marking, negative marking, attempts, permitted materials, support
contact, special requirements) is a visible "[Edit: ...]" placeholder the admin fills in or deletes. Nothing is invented.
"""

GENERAL_INSTRUCTIONS_TEMPLATE = """# General Examination Instructions

Please read every instruction on this page carefully before you begin. By starting the examination you confirm that you have read, understood and agree to follow these instructions and the examination rules.

IMPORTANT: Instructions written for this specific examination take priority over the general guidelines below.

## 1. Examination Details
- **Examination name:** [Edit: exam name]
- **Date and start time:** [Edit: date and time, or "as shown on the examination page"]
- **Total duration:** [Edit: for example 120 minutes]
- **Number of questions:** [Edit: total questions]
- **Question types:** [Edit: for example single choice, multiple choice, numerical]
- **Attempts allowed:** [Edit: number of attempts]
- **Permitted materials:** [Edit: for example None, or rough paper and a basic calculator]

## 2. Before You Begin
- Use a stable internet connection and a device that is fully charged or connected to power.
- Close other browser tabs, downloads and applications that you do not need.
- Use an up-to-date, supported web browser.
- Keep any permitted materials ready before you start.
- Sit in a quiet place where you will not be interrupted for the full duration.
- Do not start until you understand the format, duration and submission process.

## 3. Examination Duration and Timer
- The timer starts when the examination begins and keeps running until you submit.
- Refreshing the page, closing the window or losing your connection does not pause the timer.
- Keep an eye on the remaining time and plan how long to spend on each question.
- When the timer reaches zero, the examination is submitted according to the examination rules.
- Time is not extended after a technical interruption unless the administrator announces it.

IMPORTANT: Once the examination has started, the timer cannot be paused.

## 4. Answering Questions
1. Read each question completely, together with all of its options, before you answer.
2. Pay close attention to keywords, conditions, units, diagrams, tables and mathematical expressions.
3. Answer according to the question type shown:
  - Single choice: select the one option you consider correct.
  - Multiple choice: select every correct option, only when the question allows more than one.
  - Numerical: enter the value with the required sign, decimal places and units.
4. Check that your answer is shown correctly before moving to another question.
5. Do not assume that every question carries equal marks.

## 5. Navigating Between Questions
- Use the question palette or the navigation controls to move between questions.
- The palette shows which questions are answered, unanswered or marked for review.
- Use **Mark for Review** on questions you want to revisit before submitting.
- Rely on the controls inside the examination, not the browser Back or Forward buttons.
- [Edit: add any navigation limits for this exam, for example if going back is restricted, or delete this line]

## 6. Saving Answers
- Check that the option you chose or the value you entered is displayed correctly.
- Use any Save or confirmation control the examination provides.
- Do not assume unsaved work will be recovered after a crash, a lost connection or a closed window.
- If you change an answer, confirm that the new answer is displayed.

## 7. Marking Scheme
- **Marks for a correct answer:** [Edit: marks per correct answer]
- **Negative marking:** [Edit: marks deducted per wrong answer, or "none"]
- **Unanswered questions:** [Edit: for example no marks are awarded or deducted]
- **Passing criteria:** [Edit: passing percentage, or delete this line]
- The marks shown for a question are the reference for its scoring value.

## 8. Submitting Your Examination
- Before you submit, review the palette for unanswered questions and questions marked for review.
- Use the Submit or Finish control and read any confirmation message carefully.
- Do not close the browser or leave the page while submission is being processed.
- Wait for the confirmation that your examination was submitted successfully.
- A submitted examination cannot be reopened or changed.
- **Results:** [Edit: when and how results are released]

## 9. Technical Issues
- If the page stops responding, wait a short while before taking any action.
- Do not refresh repeatedly or open the examination in several windows or on several devices.
- If your connection drops, restore it and continue from the same window.
- Note the exact error message and the time it appeared, and keep a screenshot if possible.
- Report the problem as soon as possible to: [Edit: support email, phone number or contact person]
- Never share your password or login codes while asking for help.

## 10. Examination Conduct
- Attempt the examination independently, without help from any other person.
- Do not copy, photograph, record or share the questions.
- Do not try to bypass the examination controls or interfere with the system.
- Do not use unauthorized devices, websites, messaging apps or other sources of help.
- Use only the materials listed under Permitted materials above.
- Breaking these rules may lead to your attempt being cancelled.

## 11. Account Security and Privacy
- Use only your own account and keep your login details confidential.
- Sign out when you finish, especially on a shared device.
- Do not share the examination link or your personal details with others.

## 12. Additional Instructions for This Examination
- [Edit: add any exam-specific requirements here, or delete this section]

Best wishes for your examination.
"""
