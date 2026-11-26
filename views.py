import discord
from discord.ui import View, Button

class ReportView(View):
    def __init__(self, full_report: str):
        super().__init__(timeout=300)
        self.full_report = full_report

    @discord.ui.button(label="Full Detailed Report", style=discord.ButtonStyle.primary)
    async def full_report_button(self, interaction: discord.Interaction, button: Button):
        # Discord has a 2000 character limit per message, so we split if needed
        if len(self.full_report) <= 2000:
            await interaction.response.send_message(self.full_report, ephemeral=True)
        else:
            chunks = [self.full_report[i:i+1900] for i in range(0, len(self.full_report), 1900)]
            await interaction.response.send_message(chunks[0], ephemeral=True)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=True)